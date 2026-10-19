import base64
import hashlib

import pytest

from dark_mcp.config import Settings


def address(key: bytes) -> str:
    checksum = hashlib.sha3_256(b".onion checksum" + key + b"\x03").digest()[:2]
    return "http://" + base64.b32encode(key + checksum + b"\x03").decode().lower() + ".onion/"


@pytest.fixture
def onion():
    return address(b"a" * 32)


@pytest.fixture
def other_onion():
    return address(b"b" * 32)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        telegram_enabled=False,
        telegram_api_id=None,
        telegram_api_hash=None,
        onion_search_api_key=None,
    )
