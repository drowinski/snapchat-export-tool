from os import PathLike, utime
from struct import unpack_from
from zipfile import ZipFile, ZipInfo


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
