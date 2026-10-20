from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DARK_MCP_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    ail_url: str = "http://127.0.0.1:7000"
    ail_api_key: SecretStr | None = None
    ail_api_key_file: Path | None = Path("data/ail/api-key")
    ail_verify_tls: bool = True
    request_timeout: float = Field(default=45, ge=1, le=180)
    max_response_bytes: int = Field(default=4_000_000, ge=1024, le=20_000_000)

    @field_validator("ail_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Use an HTTP(S) AIL URL without credentials")
        if parts.query or parts.fragment:
            raise ValueError("AIL base URL cannot contain a query or fragment")
        return value.rstrip("/")

    def api_key(self) -> str:
        if self.ail_api_key:
            value = self.ail_api_key.get_secret_value()
        elif self.ail_api_key_file and self.ail_api_key_file.is_file():
            value = self.ail_api_key_file.read_text().strip()
        else:
            raise ValueError("Configure DARK_MCP_AIL_API_KEY or DARK_MCP_AIL_API_KEY_FILE locally")
        if not value or any(c.isspace() for c in value):
            raise ValueError("Invalid locally configured AIL API key")
        return value
