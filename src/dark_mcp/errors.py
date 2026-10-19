class DataAccessError(Exception):
    """An expected, sanitized failure that may be shown to an MCP client."""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None, retry_at: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.retry_at = retry_at

    def as_dict(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            error["retry_after_seconds"] = self.retry_after
        if self.retry_at is not None:
            error["retry_at"] = self.retry_at
        return {"ok": False, "error": error}
