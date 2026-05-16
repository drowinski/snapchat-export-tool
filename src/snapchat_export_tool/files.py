import hashlib
import logging
from collections.abc import Collection
from os import PathLike
from pathlib import Path
from typing import NamedTuple

from snapchat_export_tool.exiftool import (
    Exiftool,
    set_file_tags,
    set_image_tags,
    set_video_tags,
)
from snapchat_export_tool.metadata import MediaType, Memory

logger = logging.getLogger(__name__)


def determine_unique_files(memories_dir: str | PathLike[str]) -> set[Path]:
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
        _get_id_from_filename(p.name): p for p in memories_dir.glob("*_*-overlay.png")
    }

    class MemoryAndOverlayPaths(NamedTuple):
        memory_filepath: Path
        overlay_filepath: Path | None

    hash_to_potential_duplicates: dict[str, list[MemoryAndOverlayPaths]] = {}
    for memory_filepath in memories_dir.iterdir():
        if memory_filepath.suffix.lower() not in (".jpg", ".jpeg", ".mp4"):
            continue

        memory_id = _get_id_from_filename(memory_filepath.name)
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

    filepaths_to_keep: set[Path] = set()
    for duplicates in hash_to_potential_duplicates.values():
        if len(duplicates) == 1:
            filepaths_to_keep.add(duplicates[0].memory_filepath)
            if duplicates[0].overlay_filepath:
                filepaths_to_keep.add(duplicates[0].overlay_filepath)
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
            filepaths_to_keep.add(duplicates_without_overlay[0].memory_filepath)

        for duplicates_with_overlay in overlay_hash_to_duplicates.values():
            filepaths_to_keep.add(duplicates_with_overlay[0].memory_filepath)
            assert duplicates_with_overlay[0].overlay_filepath
            filepaths_to_keep.add(duplicates_with_overlay[0].overlay_filepath)

    return filepaths_to_keep


class FilepathWithMemory(NamedTuple):
    filepath: Path
    memory: Memory


def match_files_with_memories(
    filepaths: Collection[Path], memories: Collection[Memory]
) -> list[FilepathWithMemory]:
    id_to_overlay_filepath: dict[str, Path] = {
        _get_id_from_filename(p.name): p
        for p in filepaths
        if p.name.endswith("overlay.png")
    }

    media_type_and_timestamp_to_memory: dict[str, Memory] = {
        f"{m.media_type.name}:{m.date.timestamp()}": m for m in memories
    }

    filepaths_with_memory: list[FilepathWithMemory] = []
    for filepath in filepaths:
        suffix = filepath.suffix.lower() # Satisfy PyRight
        match suffix:
            case ".jpg" | ".jpeg":
                media_type = MediaType.IMAGE
            case ".mp4":
                media_type = MediaType.VIDEO
            case _:
                continue

        memory = media_type_and_timestamp_to_memory.get(
            f"{media_type.name}:{filepath.stat().st_mtime}"
        )

        if not memory:
            logger.warning(
                f"No memory metadata found for file: '{filepath.name}'\n"
                f"Keyed by type {media_type.name} at timestamp {filepath.stat().st_mtime}"
            )
            continue

        filepaths_with_memory.append(FilepathWithMemory(filepath, memory))

        overlay_filepath = id_to_overlay_filepath.get(
            _get_id_from_filename(filepath.name)
        )
        if overlay_filepath:
            filepaths_with_memory.append(FilepathWithMemory(overlay_filepath, memory))

    return filepaths_with_memory


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


def _get_id_from_filename(filename: str) -> str:
    return filename.split("_", 1)[1].rsplit("-", 1)[0]


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
