import hashlib
import logging
from collections.abc import Collection
from datetime import UTC, datetime
from itertools import combinations
from os import PathLike
from pathlib import Path
from typing import NamedTuple

from snapchat_export_tool.exiftool import (
    Exiftool,
    ExiftoolException,
    set_file_tags,
    set_image_tags,
    set_video_tags,
)
from snapchat_export_tool.metadata import MediaType, Memory

logger = logging.getLogger(__name__)


def determine_media_filepaths(memories_dir: str | PathLike[str]) -> list[Path]:
    return [
        p
        for p in Path(memories_dir).iterdir()
        if p.suffix in (".jpg", ".jpeg", ".mp4", ".png")
    ]


def determine_unique_media_filepaths(memories_dir: str | PathLike[str]) -> list[Path]:
    """
    Two or more memory files are duplicates when the contents
    and modification timestamps are the same.

    Behavior:
     - If no duplicate has an overlay, only keep one.
     - If any duplicate has an overlay, do not keep ones without.
     - If two or more duplicates have the same overlay, only keep one of them.
    """
    memories_dir = Path(memories_dir)

    id_to_overlay_filepath: dict[str, Path] = {
        _get_filename_without_suffix(p.name): p
        for p in memories_dir.glob("*_*-overlay.png")
    }

    class MemoryAndOverlayPaths(NamedTuple):
        memory_filepath: Path
        overlay_filepath: Path | None

    hash_to_potential_duplicates: dict[str, list[MemoryAndOverlayPaths]] = {}
    for memory_filepath in memories_dir.iterdir():
        if memory_filepath.suffix.lower() not in (".jpg", ".jpeg", ".mp4"):
            continue

        memory_id = _get_filename_without_suffix(memory_filepath.name)
        if not memory_id:
            continue

        hash_to_potential_duplicates.setdefault(
            _get_mtime_aware_file_hash(memory_filepath), []
        ).append(
            MemoryAndOverlayPaths(
                memory_filepath=memory_filepath,
                overlay_filepath=id_to_overlay_filepath.get(memory_id),
            )
        )

    filepaths_to_keep: list[Path] = []
    for duplicates in hash_to_potential_duplicates.values():
        if len(duplicates) == 1:
            filepaths_to_keep.append(duplicates[0].memory_filepath)
            if duplicates[0].overlay_filepath:
                filepaths_to_keep.append(duplicates[0].overlay_filepath)
            continue

        overlay_hash_to_duplicates: dict[str, list[MemoryAndOverlayPaths]] = {}
        for duplicate in duplicates:
            overlay_hash_to_duplicates.setdefault(
                _get_mtime_aware_file_hash(duplicate.overlay_filepath)
                if duplicate.overlay_filepath
                else "NO_OVERLAY",
                [],
            ).append(duplicate)

        duplicates_without_overlay = overlay_hash_to_duplicates.pop("NO_OVERLAY", [])

        if duplicates_without_overlay and len(overlay_hash_to_duplicates) == 0:
            filepaths_to_keep.append(duplicates_without_overlay[0].memory_filepath)

        for duplicates_with_overlay in overlay_hash_to_duplicates.values():
            filepaths_to_keep.append(duplicates_with_overlay[0].memory_filepath)
            assert duplicates_with_overlay[0].overlay_filepath
            filepaths_to_keep.append(duplicates_with_overlay[0].overlay_filepath)

    return filepaths_to_keep


class FilepathWithMemory(NamedTuple):
    filepath: Path
    memory: Memory


def match_files_with_memories(
    filepaths: Collection[Path], memories: Collection[Memory]
) -> list[FilepathWithMemory]:
    # We will be matching JSON entries to media files by type and timestamp.
    media_type_and_timestamp_to_memory_group: dict[
        tuple[MediaType, float], list[Memory]
    ] = {}
    for memory in memories:
        media_type_and_timestamp_to_memory_group.setdefault(
            (memory.media_type, memory.date.timestamp()), []
        ).append(memory)

    # We've grouped JSON entries by type and timestamp but each entry in a group
    # may still be unique based on location.
    # There is no way to determine which location belongs to which file,
    # so we consolidate each group into one Memory that works for any file.
    media_type_and_timestamp_to_memory: dict[tuple[MediaType, float], Memory] = (
        _consolidate_memory_groups_based_on_location(
            media_type_and_timestamp_to_memory_group
        )
    )

    # Overlays will receive the same metadata as the main files they belong to.
    id_to_overlay_filepath: dict[str, Path] = {
        _get_filename_without_suffix(p.name): p
        for p in filepaths
        if p.name.endswith("overlay.png")
    }

    filepaths_with_memories: list[FilepathWithMemory] = []
    for filepath in filepaths:
        suffix = filepath.suffix.lower()  # Satisfy PyRight
        match suffix:
            case ".jpg" | ".jpeg":
                media_type = MediaType.IMAGE
            case ".mp4":
                media_type = MediaType.VIDEO
            case _:
                continue

        memory = media_type_and_timestamp_to_memory.get(
            (media_type, filepath.stat().st_mtime)
        )

        if not memory:
            logger.warning(f"No memory metadata found for file: '{filepath.name}'\n")
            continue

        filepaths_with_memories.append(FilepathWithMemory(filepath, memory))

        overlay_filepath = id_to_overlay_filepath.get(
            _get_filename_without_suffix(filepath.name)
        )
        if overlay_filepath:
            filepaths_with_memories.append(FilepathWithMemory(overlay_filepath, memory))

    return filepaths_with_memories


def _consolidate_memory_groups_based_on_location(
    media_type_and_timestamp_to_memory_group: dict[
        tuple[MediaType, float], list[Memory]
    ],
) -> dict[tuple[MediaType, float], Memory]:
    result: dict[tuple[MediaType, float], Memory] = {}

    keys_of_dropped_locations: list[tuple[MediaType, float]] = []

    for key, memory_group in media_type_and_timestamp_to_memory_group.items():
        if len(memory_group) == 1:
            result[key] = memory_group[0]
            continue

        locations = [m.location for m in memory_group if m.location is not None]
        if not locations:
            result[key] = memory_group[0]
        elif len(locations) == 1:
            # If any JSON entry has a location, we pick that one.
            result[key] = next(m for m in memory_group if m.location)
        else:
            # If multiple JSON entries share the same media type and timestamp
            # but their locations differ, there is no way to match the right
            # locations to files reliably, so we strip the ones 1 km or more apart.
            any_far_apart = any(
                a.get_kilometers_from(b) >= 1 for a, b in combinations(locations, 2)
            )
            if any_far_apart:
                keys_of_dropped_locations.append(key)
            base = next(m for m in memory_group if m.location)
            result[key] = (
                base
                if not any_far_apart
                else base.model_copy(update={"location": None})
            )

    if keys_of_dropped_locations:
        entries = ", ".join(
            f"{k[0].name} at {datetime.fromtimestamp(k[1], tz=UTC).strftime('%Y-%m-%d %H:%M:%S')}"
            for k in keys_of_dropped_locations
        )
        logger.warning(
            f"{len(keys_of_dropped_locations)} JSON metadata entries"
            f" with identical timestamps have conflicting locations:\n"
            f"[{entries}]\n"
            f"No location metadata will be applied to their corresponding files."
        )

    return result


def fix_file_extension(exiftool: Exiftool, filepath: str | PathLike[str]) -> Path:
    filepath = Path(filepath)
    result = exiftool.get_file_tags(("FileTypeExtension",), filepath)

    new_extension = result.get("FileTypeExtension")
    if not new_extension:
        raise ExiftoolException("Could not determine file extension.")

    new_extension = f".{new_extension.lower()}"
    if filepath.suffix.lower() == new_extension:
        return filepath

    return filepath.rename(filepath.with_suffix(new_extension))


def write_file_metadata(
    exiftool: Exiftool, filepath: str | PathLike[str], memory: Memory
) -> None:
    filepath = Path(filepath)
    if filepath.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        set_image_tags(exiftool, filepath, memory.date, memory.location)
    elif filepath.suffix.lower() == ".mp4":
        set_video_tags(exiftool, filepath, memory.date, memory.location)
    else:
        logger.warning("Unrecognized file type: %s", filepath)

    set_file_tags(exiftool, filepath, memory.date)


def _get_filename_without_suffix(filename: str) -> str:
    return filename.rsplit("-", 1)[0]


def _get_mtime_aware_file_hash(filepath: str | PathLike[str]) -> str:
    filepath = Path(filepath)

    if filepath.suffix.lower() == ".mp4":
        # Identical MP4 files have slight differences in headers leading to unequal MD5s
        # Those differences don't seem to affect the file sizes, which paired with mtime
        # should be good enough and be less hassle than hashing streams with ffmpeg.
        return f"{filepath.stat().st_size}:{filepath.stat().st_mtime}"

    file_hash = hashlib.md5()
    with open(filepath, "rb") as file:
        while data := file.read(65536):
            file_hash.update(data)

    return f"{file_hash.hexdigest()}:{filepath.stat().st_mtime}"
