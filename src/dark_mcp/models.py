from datetime import UTC, datetime

from pydantic import BaseModel, Field


def now() -> str:
    return datetime.now(UTC).isoformat()


class OnionPage(BaseModel):
    url: str
    title: str
    text: str
    links: list[str] = Field(default_factory=list)
    collected_at: str = Field(default_factory=now)
    truncated: bool = False


def limit_value(value: int, maximum: int = 100) -> int:
    if not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def parse_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Dates must include a UTC offset, for example 2026-10-18T00:00:00Z")
    return date.astimezone(UTC)
