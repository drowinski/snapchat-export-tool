import json
import logging
from datetime import datetime
from enum import Enum
from math import acos, cos, radians, sin
from os import PathLike
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

EARTH_MEAN_RADIUS = 6371.0088


class MediaType(Enum):
    IMAGE = "Image"
    VIDEO = "Video"


class Location(BaseModel):
    latitude: float
    longitude: float

    def __hash__(self) -> int:
        return hash((self.latitude, self.longitude))

    def get_kilometers_from(self, location: "Location") -> float:
        value = sin(radians(self.latitude)) * sin(radians(location.latitude)) + cos(
            radians(self.latitude)
        ) * cos(radians(location.latitude)) * cos(
            radians(self.longitude - location.longitude)
        )
        return EARTH_MEAN_RADIUS * acos(max(-1.0, min(1.0, value)))


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
            logging.error(
                "Could not validate a Memory. The JSON might be malformed.",
                exc_info=error,
            )

    return memories


def localize_memory_dates(memories: list[Memory]) -> list[Memory]:
    result: list[Memory] = []
    with TimezoneFinder() as timezone_finder:
        for memory in memories:
            if memory.location is None:
                result.append(memory)
                continue

            timezone_str = timezone_finder.timezone_at(
                lat=memory.location.latitude, lng=memory.location.longitude
            )

            if timezone_str is None:
                result.append(memory)
                continue

            result.append(
                memory.model_copy(
                    update={"date": memory.date.astimezone(ZoneInfo(timezone_str))}
                )
            )

    return result
