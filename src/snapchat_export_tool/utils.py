from math import acos, cos, radians, sin
from os import PathLike, utime
from struct import unpack_from
from zipfile import ZipFile, ZipInfo

from snapchat_export_tool.metadata import Location

EARTH_MEAN_RADIUS = 6371.0088


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


def get_kilometers_between_locations(location1: Location, location2: Location) -> float:
    value = sin(radians(location1.latitude)) * sin(radians(location2.latitude)) + cos(
        radians(location1.latitude)
    ) * cos(radians(location2.latitude)) * cos(
        radians(location1.longitude - location2.longitude)
    )
    return EARTH_MEAN_RADIUS * acos(max(-1.0, min(1.0, value)))
