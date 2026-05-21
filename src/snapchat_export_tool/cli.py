import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from typing import Annotated

from rich.logging import RichHandler
from rich.progress import Progress
from rich.prompt import Confirm
from typer import Abort, Argument, Exit, Option, Typer

from snapchat_export_tool.exiftool import (
    Exiftool,
    ExiftoolException,
)
from snapchat_export_tool.files import (
    FilepathWithMemory,
    determine_media_filepaths,
    determine_unique_media_filepaths,
    fix_file_extension,
    match_files_with_memories,
    write_file_metadata,
)
from snapchat_export_tool.metadata import (
    load_memories_from_json,
    localize_memory_dates,
)
from snapchat_export_tool.zip import (
    extract_json_file,
    extract_media_files,
    open_zip_files,
)

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
    ignore_duplicates: Annotated[
        bool, Option(help="Do not include duplicate media files in the final output.")
    ] = False,
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
        with open_zip_files(input_paths) as zip_files:
            try:
                json_path = extract_json_file(zip_files, temp_dir)
                memories_dir = extract_media_files(zip_files, temp_dir)
            except RuntimeError as error:
                logger.error(error)
                raise Exit(code=1) from error

        logger.info("Parsing JSON...")
        try:
            memories = load_memories_from_json(json_path)
        except ValueError as error:
            logger.error(error)
            raise Exit(code=1) from error

        if localize_timestamps:
            logger.info("Adjusting timestamps based on locations...")
            memories = localize_memory_dates(memories)
        else:
            logger.warning("Location-based timestamp adjustment has been disabled.")

        if ignore_duplicates:
            logger.info("Determining duplicate files...")
            filepaths = determine_unique_media_filepaths(memories_dir)
        else:
            filepaths = determine_media_filepaths(memories_dir)

        logger.info("Matching memory metadata to files...")
        filepaths_with_memories = match_files_with_memories(filepaths, memories)

        if not filepaths_with_memories:
            logger.error("Could not match any files to metadata.")
            raise Exit(code=1)

        output_dir.mkdir(parents=True, exist_ok=True)

        process_all_files(filepaths_with_memories, output_dir, threads)

    logger.info(f"Your Memories have been saved to: '{output_dir}'.")


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
