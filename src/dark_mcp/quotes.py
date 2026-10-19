"""Index explicitly captured quotes without attributing their text to the enclosing post."""

KINDS = {"forum", "forum_feed", "telegram_web"}


def captured_quotes(kind: str, snapshot: dict):
    if kind not in KINDS or not isinstance(snapshot.get("quotes"), list):
        return
    for ordinal, quote in enumerate(snapshot["quotes"]):
        if isinstance(quote, dict) and isinstance(quote.get("text"), str) and quote["text"].strip():
            yield ordinal, quote


def index_quote_revision(db, revision_id: int, kind: str, snapshot: dict):
    """Use only saved quote text. Do not infer an origin, parse nested copies or fetch references."""
    for ordinal, quote in captured_quotes(kind, snapshot):
        text = quote["text"]
        db.execute(
            "INSERT OR IGNORE INTO evidence_quotes(revision_id,ordinal,text,search_text) VALUES(?,?,?,?)",
            (revision_id, ordinal, text, text.casefold()),
        )
