import json
import logging
import subprocess
from collections.abc import Collection
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any

from snapchat_export_tool.metadata import Location

logger = logging.getLogger(__name__)

EXIF_DATE_FORMAT = "%Y:%m:%d"
EXIF_TIME_FORMAT = "%H:%M:%S"
EXIF_DATE_TIME_FORMAT = "%Y:%m:%d %H:%M:%S"


# Tested with exiftool 13.55
class Exiftool:
    STDOUT_SENTINEL: str = "{ready}\n"
    STDERR_SENTINEL: str = "{stderr_ready}\n"

    executable_path: str = "exiftool"

    def __init__(self, executable_path: str | None = None) -> None:
        self.process: subprocess.Popen[str] | None = None
        if executable_path:
            self.executable_path = executable_path

    def __enter__(self) -> "Exiftool":
        self.__start_process()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.__stop_process()

    def start_process(self) -> None:
        self.__start_process()

    def stop_process(self) -> None:
        self.__stop_process()

    def get_file_tags(
        self, tags: Collection[str], filepath: str | PathLike[str]
    ) -> dict[str, str]:
        if len(tags) == 0:
            raise RuntimeError("No tags specified.")

        filepath = Path(filepath).absolute()

        if not filepath.is_file():
            raise FileNotFoundError(
                f'Cannot set tags because the file does not exist: "{filepath}".'
            )

        args: list[str] = [f"-{tag}" for tag in tags]
        args.append(str(filepath))

        result = self.execute(args)
        if not result or len(result) == 0:
            return {}

        return result[0]

    def set_file_tags(
        self, tags: dict[str, str], filepath: str | PathLike[str]
    ) -> None:
        if len(tags) == 0:
            raise RuntimeError("No tags specified.")

        filepath = Path(filepath).absolute()

        if not filepath.is_file():
            raise FileNotFoundError(
                f'Cannot set tags because the file does not exist: "{filepath}".'
            )

        args: list[str] = [f"-{key}={value}" for key, value in tags.items()]
        args.append(str(filepath))

        self.execute(args)

    def __start_process(self) -> None:
        if self.process is not None:
            return

        try:
            self.process = subprocess.Popen(
                (
                    self.executable_path,
                    "-stay_open",
                    "True",
                    "-@",
                    "-",
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise ExiftoolException(
                "Exiftool executable not found. Make sure it is installed and in your path."
            ) from e

    def __stop_process(self) -> None:
        if (
            self.process is None
            or self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            return

        self.process.stdin.write("-stay_open\nFalse\n")
        self.process.stdin.close()

        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait()

        logger.debug(f"Exiftool exited with code: {self.process.returncode}")

        self.process = None

    def execute(self, args: Collection[str]) -> list[dict[str, Any]] | None:
        args = list(args)

        if (
            self.process is None
            or self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            raise RuntimeError("ExifTool instance not running.")

        args += (
            "-overwrite_original",
            "-json",  # Output JSON.
            "--printConv",  # Disable conversion to human-readable values.
            "-q",  # Quiet mode to prevent "x image files updated" messages in stderr.
            "-echo3",  # Once stdout completes...
            Exiftool.STDOUT_SENTINEL.strip(),  # ...echo the sentinel. This is the default behavior but -q disabled it.
            "-echo4",  # Once stderr completes...
            Exiftool.STDERR_SENTINEL.strip(),  # ...echo the custom sentinel.
            "-execute",
        )

        arg_str: str = "\n".join(args) + "\n"

        self.process.stdin.write(arg_str)

        error = ""
        while not error.endswith(Exiftool.STDERR_SENTINEL):
            error += self.process.stderr.readline()
        error = error.removesuffix(Exiftool.STDERR_SENTINEL).strip()

        if len(error) > 0:
            raise ExiftoolException(error)

        output = ""
        while not output.endswith(Exiftool.STDOUT_SENTINEL):
            output += self.process.stdout.readline()
        output = output.removesuffix(Exiftool.STDOUT_SENTINEL).strip()

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return None


class ExiftoolException(Exception):
    pass


def set_file_tags(
    exiftool: Exiftool,
    filepath: str | PathLike[str],
    date_time: datetime,
) -> None:
    date_time_str = date_time.strftime(EXIF_DATE_TIME_FORMAT) + _format_timezone(
        date_time
    )
    tags: dict[str, str] = {
        "File:FileModifyDate": date_time_str,
        "File:FileCreateDate": date_time_str,
    }

    logger.debug(f"Setting EXIF tags of '{filepath}': {tags}")

    exiftool.set_file_tags(tags, filepath)


def set_image_tags(
    exiftool: Exiftool,
    filepath: str | PathLike[str],
    date_time: datetime | None = None,
    location: Location | None = None,
) -> None:
    if date_time is None and location is None:
        raise RuntimeError("Must specify at least one of date_time or location.")

    tags: dict[str, str] = {}

    if date_time is not None:
        date_time_str: str = date_time.strftime(EXIF_DATE_TIME_FORMAT)

        # Local time
        tags["EXIF:DateTimeOriginal"] = date_time_str
        tags["EXIF:CreateDate"] = date_time_str
        tags["EXIF:ModifyDate"] = date_time_str

        if (
            date_time.tzinfo is not None
            and date_time.tzinfo.utcoffset(date_time) is not None
        ):
            timezone_str: str = _format_timezone(date_time)
            tags["EXIF:OffsetTimeOriginal"] = timezone_str
            tags["EXIF:OffsetTimeDigitized"] = timezone_str
            tags["EXIF:OffsetTime"] = timezone_str

    if location is not None:
        tags["EXIF:GPSLatitude"] = str(abs(location.latitude))
        tags["EXIF:GPSLongitude"] = str(abs(location.longitude))
        tags["EXIF:GPSLatitudeRef"] = "N" if location.latitude > 0 else "S"
        tags["EXIF:GPSLongitudeRef"] = "E" if location.longitude >= 0 else "W"

        if date_time is not None:
            date_time_utc: datetime = date_time.astimezone(UTC)
            tags["EXIF:GPSDateStamp"] = date_time_utc.strftime(EXIF_DATE_FORMAT)
            tags["EXIF:GPSTimeStamp"] = date_time_utc.strftime(EXIF_TIME_FORMAT)

    logger.debug(f"Setting EXIF tags of '{filepath}': {tags}")

    exiftool.set_file_tags(tags, filepath)


def set_video_tags(
    exiftool: Exiftool,
    filepath: str | PathLike[str],
    date_time: datetime | None = None,
    location: Location | None = None,
) -> None:
    if date_time is None and location is None:
        raise RuntimeError("Must specify at least one of date_time or location.")

    tags: dict[str, str] = {}

    if date_time is not None:
        is_date_time_aware = (
            date_time.tzinfo is not None
            and date_time.tzinfo.utcoffset(date_time) is not None
        )

        if is_date_time_aware:
            # Local time with timezone info
            tags["Keys:CreationDate"] = date_time.strftime(
                EXIF_DATE_TIME_FORMAT
            ) + _format_timezone(date_time)

        date_time_utc: datetime = (
            date_time.astimezone(UTC) if is_date_time_aware else date_time
        )
        date_time_utc_str: str = date_time_utc.strftime(EXIF_DATE_TIME_FORMAT)

        # UTC without timezone info
        tags["QuickTime:CreateDate"] = date_time_utc_str
        tags["QuickTime:ModifyDate"] = date_time_utc_str

    if location is not None:
        gps_coordinates_str: str = f"{location.latitude:.5f} {location.longitude:.5f} 0"
        tags["Keys:GPSCoordinates"] = gps_coordinates_str
        tags["ItemList:GPSCoordinates"] = gps_coordinates_str
        tags["UserData:GPSCoordinates"] = gps_coordinates_str

    logger.debug(f"Setting EXIF tags of '{filepath}': {tags}")

    exiftool.set_file_tags(tags, filepath)


def _format_timezone(date_time: datetime) -> str:
    timezone_str: str = date_time.strftime("%z")

    if not timezone_str:
        raise RuntimeError("Cannot format timezone of naive datetime.")

    timezone_str = f"{timezone_str[:3]}:{timezone_str[3:]}"

    return timezone_str
