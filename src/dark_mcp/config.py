from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DARK_MCP_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    data_dir: Path = Path.home() / ".local/share/dark-mcp"
    tor_proxy: str = "socks5h://127.0.0.1:9050"
    request_timeout: float = Field(default=45, ge=1, le=120)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    max_text_chars: int = Field(default=60_000, ge=100, le=200_000)
    onion_search_api_key: SecretStr | None = None
    telegram_api_id: int | None = Field(default=None, gt=0)
    telegram_api_hash: SecretStr | None = None
    telegram_enabled: bool = False
    telegram_channels: list[str] = []
    telegram_catalog_sources: bool = False
    telegram_global_search: bool = False

    def prepare_data_dir(self) -> Path:
        path = self.data_dir.expanduser()
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @property
    def session_path(self) -> Path:
        return self.data_dir.expanduser() / "telegram.session"
