import json
import logging
from datetime import datetime
from enum import Enum
from os import PathLike
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)


class MediaType(Enum):
    IMAGE = "Image"
    VIDEO = "Video"


class Location(BaseModel):
    latitude: float
    longitude: float

    def __hash__(self) -> int:
        return hash((self.latitude, self.longitude))


class Memory(BaseModel):
    date: datetime = Field(alias="Date")
    media_type: MediaType = Field(alias="Media Type")
    location: Location | None = Field(alias="Location")

    @field_validator("date", mode="before")
    @classmethod
    def parse_date(cls, v: str) -> datetime:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S %Z").replace(
            tzinfo=ZoneInfo("UTC")
        )

    @model_validator(mode="before")
    @classmethod
    def parse_location(
        cls, data: dict[str, str | float | Location | None]
    ) -> dict[str, str | float | Location | None]:
        location = data.pop("Location", None)
        if isinstance(location, str):
            _, coords = location.split(": ")
            latitude, longitude = coords.split(", ")
            latitude = float(latitude)
            longitude = float(longitude)
            if not (latitude == 0.0 and longitude == 0.0):
                data["Location"] = Location(latitude=latitude, longitude=longitude)
            else:
                data["Location"] = None
        else:
            data["Location"] = None
        return data


def load_memories_from_json(
    memories_history_filepath: str | PathLike[str],
) -> list[Memory]:
    with open(memories_history_filepath) as memories_history_file:
        memories_history_json: dict[str, list[dict[str, str]]] = json.load(
            memories_history_file
        )

    json_memories = memories_history_json.get("Saved Media")

    if json_memories is None:
        raise ValueError("'Saved Media' field not found in JSON.")

    memories: list[Memory] = []
    for json_memory in json_memories:
        try:
            memories.append(Memory.model_validate(json_memory))
        except ValidationError as error:
            logging.error("Could not validate memory.", exc_info=error, stack_info=True)

    return memories


def localize_date(
    memory: Memory, timezone_finder: TimezoneFinder | None = None
) -> Memory:
    if memory.location is None:
        return memory

    if not timezone_finder:
        timezone_finder = TimezoneFinder()

    timezone_str = timezone_finder.timezone_at(
        lat=memory.location.latitude, lng=memory.location.longitude
    )

    if timezone_str is None:
        return memory

    return memory.model_copy(
        update={"date": memory.date.astimezone(ZoneInfo(timezone_str))}
    )
