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
    next_page_urls: list[str] = Field(default_factory=list)
    published_at: str | None = None
    access_state: str = "content_returned_unverified"
    evidence: dict | None = None


class ForumPost(BaseModel):
    id: str
    url: str
    author: str | None = None
    text: str
    published_at: str | None = None
    published_at_raw: str | None = None
    published_at_hint: str | None = None
    timestamp_basis: str | None = None
    is_thread_root: bool | None = None
    post_number: int | None = Field(default=None, ge=1, le=1_000_000_000)
    post_type: str | None = None
    reply_to_post_id: str | None = None
    source_events: list[dict] = Field(default_factory=list)
    edited_at: str | None = None
    edited_at_raw: str | None = None
    hidden_content: bool = False
    quotes: list[dict] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)
    truncated: bool = False
    evidence: dict | None = None


class ForumPage(BaseModel):
    source_id: str
    url: str
    title: str
    text: str
    page_type: str
    thread_key: str | None = None
    posts: list[ForumPost] = Field(default_factory=list)
    threads: list[dict] = Field(default_factory=list)
    thread_listing_scope: str = "unreported"
    omitted_threads: int = 0
    sections: list[dict] = Field(default_factory=list)
    next_page_urls: list[str] = Field(default_factory=list)
    access_state: str
    truncated: bool = False
    omitted_posts: int = 0
    published_at: str | None = None
    collected_at: str = Field(default_factory=now)
    evidence: dict | None = None
    transport: str = "direct_https"
    access_context: dict = Field(default_factory=dict)
    coverage: str


def classify_page(title: str, text: str) -> str:
    title, text = title.casefold(), text.casefold()
    if not text.strip():
        return "empty_document"
    if any(
        phrase in title or phrase in text[:300]
        for phrase in (
            "verify humanity",
            "checking your browser",
            "just a moment...",
            "security check",
            "проверка браузера",
        )
    ):
        return "challenge_suspected"
    if len(text) < 1500 and any(
        phrase in title or phrase in text
        for phrase in (
            "log in",
            "logged in",
            "login",
            "sign in",
            "вход",
            "forgot my password",
        )
    ):
        return "login_gate_suspected"
    return "content_returned_unverified"


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
