"""Download only pinned upstream research artifacts, never channel content or attachments."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import httpx

from dark_mcp.errors import DataAccessError
from dark_mcp.models import now
from dark_mcp.open_telegram import PROVIDERS, TGDATASET_PREVIEW_SHA256

SNAPSHOTS = {
    "darkgram": {
        "repository": "SayakSR/DarkGram",
        "commit": "dcf53be230b4d6191c551ed54e9733c847b918c2",
        "files": {
            "data/posts.zip": "a77a496266d7f27481b596655607f066705719c647c6704d760b8feebcc08e0a",
        },
    },
    "deepdarkcti": {
        "repository": "fastfire/deepdarkCTI",
        "commit": "2ac6ba6cd61ffd9290519fb4a7bcccb28c5ad5f2",
        "files": {
            "telegram_threat_actors.md": "16ecf6d408b7f752af1c816bbbf64a57c9511db0bbd173239967e6346cd2f349",
            "telegram_infostealer.md": "ce3e470bafe3650e603126bea84ebc76ae3ca432fdd61923517f150f5ec13c29",
        },
    },
    "tgdataset": {
        "repository": "SystemsLab-Sapienza/TGDataset",
        "commit": "7262c20bacb3addcb82080acb371c4d2ed08df14",
        "files": {
            "tgdataset_sample/tgdataset_preview.json": TGDATASET_PREVIEW_SHA256,
        },
    },
}


def private_write(path: Path, raw: bytes):
    # Atomic replacement without exposing an incompletely downloaded artifact as ready to import.
    descriptor, name = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def fetch_open_artifacts(settings, dataset: str) -> dict:
    if dataset not in SNAPSHOTS:
        raise ValueError("Use darkgram, deepdarkcti or tgdataset")
    snapshot = SNAPSHOTS[dataset]
    directory = settings.prepare_data_dir() / "open-telegram" / dataset / snapshot["commit"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    receipts = []
    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=False, trust_env=False
        ) as client:
            for member, expected in snapshot["files"].items():
                url = f"https://raw.githubusercontent.com/{snapshot['repository']}/{snapshot['commit']}/{member}"
                path = directory / Path(member).name
                # Existing artifacts are also size bounded and verified before reuse.
                raw = b""
                if path.exists():
                    with path.open("rb") as stream:
                        raw = stream.read(25_000_001)
                cached = len(raw) <= 25_000_000 and hashlib.sha256(raw).hexdigest() == expected
                if not cached:
                    content = bytearray()
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > 25_000_000:
                                raise ValueError("Oversized research artifact")
                    raw = bytes(content)
                    if hashlib.sha256(raw).hexdigest() != expected:
                        raise ValueError("Research artifact checksum mismatch")
                    private_write(path, raw)
                path.chmod(0o600)
                receipts.append(
                    {
                        "path": str(path.resolve()),
                        "upstream_url": url,
                        "sha256": expected,
                        "bytes": len(raw),
                        "cached": cached,
                    }
                )
    except (httpx.HTTPError, ValueError, OSError):
        raise DataAccessError(
            "open_artifact_unavailable", "Pinned research download failed; no channel content or attachments requested"
        ) from None
    result = {
        "dataset": dataset,
        "version": snapshot["commit"],
        "checked_at": now(),
        "provider": PROVIDERS[dataset],
        "files": receipts,
        "imported": False,
        "telegram_requests": 0,
        "coverage": "Pinned research artifacts only; TGDataset is the small published preview, not the full corpus.",
    }
    private_write(directory / "manifest.json", json.dumps(result, indent=2).encode())
    return result
