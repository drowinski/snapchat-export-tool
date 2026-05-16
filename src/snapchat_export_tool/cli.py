import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from typing import Annotated
from zipfile import BadZipFile, ZipFile

from rich.logging import RichHandler
from rich.progress import Progress
from rich.prompt import Confirm
from timezonefinder import TimezoneFinder
from typer import Abort, Argument, Exit, Option, Typer

from snapchat_export_tool.exiftool import (
    Exiftool,
    ExiftoolException,
    fix_file_extension,
)
from snapchat_export_tool.files import (
    FilepathWithMemory,
    determine_unique_files,
    match_files_with_memories,
    write_file_metadata,
)
from snapchat_export_tool.metadata import load_memories_from_json, localize_date
from snapchat_export_tool.utils import extract_with_timestamp

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_time=False)],
)

logger = logging.getLogger()

DEFAULT_OUTPUT_DIR_NAME = "snapchat_export_tool_output"

app = Typer(no_args_is_help=True, rich_markup_mode=None)


@app.command()
def main(
    input_paths: Annotated[
        list[Path],
        Argument(
            metavar="INPUTS", help="Zip files exported from Snapchat's My Data page."
        ),
    ],
    output_dir: Annotated[
        Path | None,
        Option(
            "--output", "-o", help="Set the output directory for processed Memories."
        ),
    ] = None,
    localize_timestamps: Annotated[
        bool,
        Option(help="Correct Memory timestamps based on timezones."),
    ] = True,
    threads: Annotated[
        int,
        Option(
            help="Set the maximum number of threads used for file operations.",
            min=1,
            max=32,
        ),
    ] = 8,
    debug: Annotated[bool, Option(hidden=True)] = False,
) -> None:
    if debug:
        logger.setLevel(logging.DEBUG)

    output_dir = output_dir or Path.cwd() / DEFAULT_OUTPUT_DIR_NAME

    if output_dir.is_dir() and any(output_dir.iterdir()):
        logger.warning(f"Output directory '{output_dir}' is not empty.")
        if not Confirm.ask("Some files may be overwritten. Proceed?"):
            raise Abort()

    with TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)

        logger.info("Extracting files...")
        extract_zip_files(input_paths, temp_dir)

        json_path = temp_dir / "json" / "memories_history.json"
        memories_dir = temp_dir / "memories"

        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Parsing JSON...")
        memories = load_memories_from_json(json_path)

        if localize_timestamps:
            logger.info("Adjusting timestamps based on locations...")
            with TimezoneFinder() as timezone_finder:
                for index in range(len(memories)):
                    memories[index] = localize_date(memories[index], timezone_finder)
        else:
            logger.warning("Location-based timestamp adjustment has been disabled.")

        logger.info("Finding duplicate files...")
        filepaths = determine_unique_files(memories_dir)

        logger.info("Matching memory metadata to files...")
        filepaths_with_memories = match_files_with_memories(filepaths, memories)

        if not filepaths_with_memories:
            logger.error("Could not match any files to metadata.")
            raise Exit(code=1)

        process_all_files(filepaths_with_memories, output_dir, threads)

    logger.info(f"Your Memories have been saved to: '{output_dir}'.")


def extract_zip_files(input_paths: list[Path], temp_dir: Path) -> None:
    zip_files: list[ZipFile] = []
    for input_path in input_paths:
        try:
            zip_files.append(ZipFile(input_path))
        except (IsADirectoryError, BadZipFile) as error:
            logger.error(f"'{input_path}' is not a valid zip file.")
            raise Exit(code=1) from error

    zip_file_with_json: ZipFile | None = next(
        filter(lambda z: "json/memories_history.json" in z.namelist(), zip_files),
        None,
    )

    if not zip_file_with_json:
        logger.error("'memories_history.json' not found.")
        raise Exit(code=1)

    zip_file_with_json.extract("json/memories_history.json", temp_dir)

    any_memories_found = False
    for zip_file in zip_files:
        memories = tuple(m for m in zip_file.namelist() if m.startswith("memories/"))
        any_memories_found = any_memories_found or len(memories) > 0
        for file_info in zip_file.infolist():
            try:
                extract_with_timestamp(zip_file, file_info, temp_dir)
            except RuntimeError:
                logger.warning(
                    f"Could not extract timestamp for file '{file_info.filename}'."
                    f" This file will not be processed."
                )
                continue
        zip_file.close()

    if not any_memories_found:
        logger.error("No media files found.")
        raise Exit(code=1)


def process_all_files(
    filepaths_with_memories: list[FilepathWithMemory],
    output_dir: Path,
    threads: int,
) -> None:
    progress = Progress(speed_estimate_period=4)
    progress_task = progress.add_task(
        "Applying metadata...", total=len(filepaths_with_memories)
    )

    on_processed_data = {"completed": 0}
    on_processed_lock = Lock()

    def on_file_processed() -> None:
        with on_processed_lock:
            on_processed_data["completed"] += 1
            progress.update(progress_task, completed=on_processed_data["completed"])

    cancel_event = Event()

    def process_chunk(matched: list[FilepathWithMemory]) -> None:
        with Exiftool() as exiftool:
            for match in matched:
                if cancel_event.is_set():
                    return
                filepath: Path = match.filepath
                try:
                    filepath = fix_file_extension(exiftool, match.filepath)
                    write_file_metadata(exiftool, filepath, match.memory)
                except ExiftoolException as e:
                    logger.error(e)
                shutil.copy2(filepath, output_dir)
                on_file_processed()

    chunk_size = ceil(len(filepaths_with_memories) / threads)
    chunks = [
        filepaths_with_memories[i : i + chunk_size]
        for i in range(0, len(filepaths_with_memories), chunk_size)
    ]
    executor = ThreadPoolExecutor(max_workers=threads)
    futures = [executor.submit(process_chunk, chunk) for chunk in chunks]

    try:
        progress.start()
        for future in as_completed(futures):
            future.result()
        exit_with = Exit(code=0)
    except KeyboardInterrupt:
        cancel_event.set()
        progress.stop()
        logger.info("Aborting...")
        exit_with = Abort()
    except ExiftoolException as exception:
        cancel_event.set()
        progress.update(progress_task, visible=False)
        progress.stop()
        logger.error(exception)
        exit_with = Exit(code=1)
    finally:
        progress.stop()
        executor.shutdown(wait=False, cancel_futures=True)
        wait(futures, timeout=10)

    raise exit_with
