import logging
from collections.abc import Generator
from contextlib import contextmanager
from os import PathLike, utime
from pathlib import Path
from struct import unpack_from
from zipfile import BadZipFile, ZipFile, ZipInfo

logger = logging.getLogger(__name__)


@contextmanager
def open_zip_files(input_paths: list[Path]) -> Generator[list[ZipFile], None, None]:
    zip_files: list[ZipFile] = []
    try:
        for input_path in input_paths:
            try:
                zip_files.append(ZipFile(input_path))
            except (IsADirectoryError, BadZipFile) as error:
                raise RuntimeError(f"{input_path} is not a valid zip file.") from error
        yield zip_files
    finally:
        for zip_file in zip_files:
            zip_file.close()


def extract_json_file(zip_files: list[ZipFile], temp_dir: Path) -> Path:
    zip_file_with_json: ZipFile | None = next(
        filter(lambda z: "json/memories_history.json" in z.namelist(), zip_files),
        None,
    )

    if not zip_file_with_json:
        raise RuntimeError("'memories_history.json' not found.")

    return Path(zip_file_with_json.extract("json/memories_history.json", temp_dir))


def extract_media_files(zip_files: list[ZipFile], temp_dir: Path) -> Path:
    any_memories_found = False
    for zip_file in zip_files:
        memory_file_infos = tuple(
            f for f in zip_file.infolist() if f.filename.startswith("memories/")
        )
        any_memories_found = any_memories_found or len(memory_file_infos) > 0
        for file_info in memory_file_infos:
            try:
                extract_with_timestamp(zip_file, file_info, temp_dir)
            except RuntimeError:
                logger.warning(
                    f"Could not extract exact timestamp for file '{file_info.filename}'."
                    f" This file will not be processed."
                )
                continue

    if not any_memories_found:
        raise RuntimeError("No media files found.")

    return Path(temp_dir) / "memories"


def extract_with_timestamp(
    zip_file: ZipFile,
    member: str | ZipInfo,
    path: str | PathLike[str] | None = None,
    pwd: bytes | None = None,
) -> str:
    if isinstance(member, str):
        member = zip_file.getinfo(member)
    timestamp = get_extended_timestamp(member)
    if not timestamp:
        raise RuntimeError(f"Could not extract timestamp from {member.filename}.")
    path = zip_file.extract(member, path, pwd)
    utime(path, (timestamp, timestamp))
    return path


def get_extended_timestamp(zip_info: ZipInfo) -> int | None:
    extra = zip_info.extra
    pos = 0
    while pos < len(extra) - 4:
        header_id = unpack_from("<H", extra, pos)[0]
        size = unpack_from("<H", extra, pos + 2)[0]
        if header_id == 0x5455:
            flags = extra[pos + 4]
            if flags & 0x1:
                return unpack_from("<i", extra, pos + 5)[0]
        pos += 4 + size
    return None
