"""Operator-directed, offline imports of open Telegram research artifacts.

No Telegram requests, channel joins, attachment downloads or allowlist changes.
Archive identities are separate from native observations, including when IDs coincide.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.models import limit_value, now, parse_date

MAX_FILE_BYTES = 100_000_000
MAX_EXPANDED_BYTES = 150_000_000
MAX_ROWS = 200_000
TGDATASET_PREVIEW_SHA256 = "515b38487c82637ad7f360180ca363136005bcec8c1e8bd53308c7a393a797f5"
PROVIDERS = {
    "darkgram": {
        "url": "https://github.com/SayakSR/DarkGram",
        "license": "Apache-2.0",
        "license_basis": "https://zenodo.org/records/14736880",
        "citation": "Sayak Saha Roy et al., DarkGram, USENIX Security 2025",
    },
    "deepdarkcti": {
        "url": "https://github.com/fastfire/deepdarkCTI",
        "license": "GPL-3.0",
        "license_basis": "upstream_repository_license",
    },
    "tgdataset": {
        "url": "https://github.com/SystemsLab-Sapienza/TGDataset",
        "license": "CC-BY-4.0",
        "license_basis": "https://zenodo.org/records/7640712",
        "citation": "Massimo La Morgia, Alessandro Mei, Alberto Maria Mongardini, TGDataset, KDD 2025",
    },
}
COVERAGE = (
    "Historical third-party research artifacts, not native Telegram observations or current access. "
    "Directory status and actor labels are upstream assertions. Imports never join channels, fetch links, "
    "change allowlists or prove pre-disclosure detection. Local observation times are import times. "
    "Missing timezone/identity metadata remains unknown; public availability does not establish reuse rights."
)


def read_artifact(file: Path) -> tuple[bytes, str]:
    try:
        with file.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if not raw or len(raw) > MAX_FILE_BYTES:
            raise ValueError
        return raw, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        raise DataAccessError("invalid_telegram_artifact", "Use a nonempty local artifact up to 100 MB") from None


def public_target(url: str) -> tuple[str, str] | None:
    """Accept only Telegram public handles or invite locators, never action URLs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc not in {"t.me", "telegram.me"} or parts.query or parts.fragment:
        return None
    target = parts.path.strip("/")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", target):
        return "public_handle", target.lower()
    if re.fullmatch(r"(?:\+|joinchat/)[A-Za-z0-9_-]{8,128}", target):
        return "invite", target
    return None


def published_date(value) -> tuple[str | None, str | None]:
    """Epochs have a UTC basis; timezone-free strings are retained without inventing an offset."""
    if value is None or value == "":
        return None, None
    raw = str(value)
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, UTC).isoformat(), "dataset_unix_seconds"
        parsed = parse_date(raw)
        return parsed.isoformat(), "dataset_explicit_offset"
    except (ValueError, TypeError, OverflowError, OSError):
        return None, "dataset_timezone_or_date_unresolved"


def darkgram_rows(raw: bytes):
    """Read CSV members in place; never extract a ZIP or evaluate serialized Python fields."""
    csv.field_size_limit(MAX_FILE_BYTES)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if (
                len(members) > 2000
                or len(names) != len(set(names))
                or sum(m.file_size for m in members) > MAX_EXPANDED_BYTES
                or any(m.flag_bits & 1 for m in members)
            ):
                raise ValueError
            files = []
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise ValueError
                if member.is_dir() or member.filename.startswith("__MACOSX/"):
                    continue
                if path.suffix != ".csv" or len(path.parts) != 3 or path.parts[0] != "posts":
                    raise ValueError
                files.append(member)
            if not files:
                raise ValueError
            # Validate all headers before yielding anything for an unsupported archive.
            for member in files:
                with archive.open(member) as stream:
                    header = next(csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig")), [])
                    if not {"Channel ID", "Post ID", "date", "message", "URL"}.issubset(header) and not {
                        "id",
                        "date",
                        "message",
                    }.issubset(header):
                        raise ValueError
            for member in sorted(files, key=lambda m: m.filename):
                with archive.open(member) as stream:
                    for line, row in enumerate(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")), 2):
                        yield (
                            member.filename,
                            line,
                            {
                                "channel": row.get("Channel ID", PurePosixPath(member.filename).stem),
                                "handle_basis": "dataset_column" if "Channel ID" in row else "dataset_member_filename",
                                "message_id": row.get("Post ID", row.get("id")),
                                "text": row.get("message"),
                                "date": row.get("date"),
                                "channel_url": row.get("URL"),
                                "reported_channel_id": row.get("peer_channel"),
                                "category": PurePosixPath(member.filename).parts[1],
                                "transformation": "upstream_research_processing_obfuscation_possible",
                            },
                        )
    except (zipfile.BadZipFile, UnicodeError, csv.Error, KeyError, RuntimeError, StopIteration):
        raise ValueError("Unsupported DarkGram posts ZIP") from None


def tgdataset_rows(raw: bytes):
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError
    for channel_id, channel in document.items():
        if not isinstance(channel, dict) or not isinstance(channel.get("text_messages"), dict):
            raise ValueError
        for message_id, message in channel["text_messages"].items():
            if not isinstance(message, dict):
                raise ValueError
            yield (
                "json_shard",
                f"{channel_id}:{message_id}",
                {
                    "channel": channel.get("username"),
                    "message_id": message_id,
                    "text": message.get("message"),
                    "date": message.get("date"),
                    "reported_channel_id": channel_id,
                    "category": "unclassified",
                    # Forwarded origin metadata is retained separately, never treated as this message's publication.
                    "forward": {
                        k: message.get(k)
                        for k in ("is_forwarded", "forwarded_from_id", "forwarded_message_date")
                        if k in message
                    },
                    "transformation": "upstream_dataset_snapshot",
                    "handle_basis": "dataset_column",
                },
            )


def import_archive(
    settings,
    file: Path,
    dataset: str,
    version: str,
    offset: int = 0,
    limit: int = 1000,
    category: str | None = None,
) -> dict:
    """Bounded deterministic row offsets; reimporting identical rows reuses immutable revisions."""
    if (
        dataset not in {"darkgram", "tgdataset"}
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", version)
        or not 0 <= offset < MAX_ROWS
        or not 1 <= limit <= 100_000
        or (category is not None and (not category.strip() or len(category) > 100))
    ):
        raise ValueError("Check dataset, version, offset, limit and category")
    raw, digest = read_artifact(file)
    provider = PROVIDERS[dataset]
    store = EvidenceStore(settings.prepare_data_dir())
    counts = Counter()
    next_offset = None
    selected = 0
    sources = set()
    try:
        rows = darkgram_rows(raw) if dataset == "darkgram" else tgdataset_rows(raw)
        for ordinal, (member, line, row) in enumerate(rows):
            if ordinal >= MAX_ROWS:
                raise ValueError("Artifact exceeds row bound")
            if ordinal < offset:
                continue
            if ordinal >= offset + limit:
                next_offset = ordinal
                break
            counts["scanned"] += 1
            if category is not None and row["category"] != category:
                counts["category_skipped"] += 1
                continue
            text, message_id, channel = row.get("text"), str(row.get("message_id", "")), row.get("channel")
            reported_id = str(row.get("reported_channel_id") or "")
            if not isinstance(text, str) or not text.strip():
                counts["empty_text_skipped"] += 1
                continue
            raw_message_id = message_id
            integer_id = re.fullmatch(r"([1-9][0-9]{0,15})(?:\.0+)?", message_id)
            if not integer_id:
                counts["invalid_identity_skipped"] += 1
                continue
            message_id = integer_id.group(1)
            target = public_target("https://t.me/" + channel) if isinstance(channel, str) else None
            handle = channel if target and target[0] == "public_handle" else None
            if not handle and not re.fullmatch(r"-?[1-9][0-9]{0,18}", reported_id):
                counts["invalid_identity_skipped"] += 1
                continue
            identity = json.dumps([handle, reported_id, member], separators=(",", ":"))
            source_id = (
                f"telegram-archive:{dataset}:{digest[:16]}:" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            )
            channel_url = "https://t.me/" + handle if handle else None
            candidate_url = f"{channel_url}/{message_id}" if channel_url else None
            acquisition = {
                "method": "open_dataset_import",
                "dataset": dataset,
                "dataset_url": provider["url"],
                "dataset_version": version,
                "dataset_variant": (
                    "published_preview"
                    if dataset == "tgdataset" and digest == TGDATASET_PREVIEW_SHA256
                    else "operator_supplied_shard"
                    if dataset == "tgdataset"
                    else "posts_release"
                ),
                "artifact_sha256": digest,
                "member": member,
                "record": str(line),
                "license": provider["license"],
                "license_basis": provider["license_basis"],
                "citation": provider["citation"],
                "network_read": False,
                "source_identity_verified": False,
                "original_capture_time_verified": False,
                "url_basis": "unverified_dataset_handle_candidate" if candidate_url else "dataset_record_only",
                "handle_basis": row["handle_basis"],
                "transformation": row["transformation"],
            }
            if source_id not in sources:
                store.register(
                    source_id,
                    "telegram_archive",
                    channel_url,
                    handle or reported_id,
                    {
                        "dataset": dataset,
                        "artifact_sha256": digest,
                        "category": row["category"],
                        "handle": handle,
                        "reported_channel_id": reported_id,
                        "dataset_version": version,
                        "acquisition": acquisition,
                        "coverage": COVERAGE,
                    },
                )
                sources.add(source_id)
            published_at, basis = published_date(row.get("date"))
            snapshot = {
                "text": text[: settings.max_text_chars],
                "title": "",
                "message_id": message_id,
                "reported_channel_id": reported_id,
                "reported_handle": handle,
                "url": candidate_url,
                "dataset_record": {"member": member, "record": str(line)},
                "published_at": published_at,
                "published_at_raw": str(row.get("date")) if row.get("date") is not None else None,
                "timestamp_basis": basis,
                "truncated": len(text) > settings.max_text_chars,
                "access_state": "content_returned_unverified",
                "forward": row.get("forward", {}),
                "acquisition": acquisition,
            }
            if message_id != raw_message_id:
                snapshot["message_id_raw"] = raw_message_id
                snapshot["message_id_basis"] = "dataset_integral_decimal_string"
            receipt = store.observe(
                source_id,
                "telegram_archive",
                f"{dataset}:{digest}:{member}:{line}",
                candidate_url or f"{provider['url']}#sha256={digest}&record={line}",
                snapshot,
                provenance=acquisition,
            )
            counts[receipt["change_type"]] += 1
            selected += 1
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        raise DataAccessError(
            "invalid_telegram_archive",
            "Unsupported or malformed artifact; prior bounded rows may have been saved. Reimport is idempotent.",
        ) from None
    return {
        "dataset": dataset,
        "dataset_version": version,
        "artifact_sha256": digest,
        "offset": offset,
        "next_offset": next_offset,
        "category": category,
        "imported_rows": selected,
        "sources_in_batch": len(sources),
        "counts": dict(counts),
        "network_read": False,
        "coverage": COVERAGE,
    }


def import_directory(settings, file: Path, version: str, collection: str) -> dict:
    if collection not in {"telegram_threat_actors", "telegram_infostealer"} or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}", version
    ):
        raise ValueError("Use a supported deepdarkCTI collection and a version identifier")
    raw, digest = read_artifact(file)
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise DataAccessError("invalid_telegram_directory", "Use upstream UTF-8 Markdown") from None
    if len(lines) > MAX_ROWS or not lines or "telegram" not in lines[0].lower():
        raise DataAccessError("invalid_telegram_directory", "Use an upstream Telegram Markdown table")
    store = EvidenceStore(settings.prepare_data_dir())
    counts = Counter()
    imported_at = now()
    assertions = {}
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and public_target(cells[0]):
            assertions.setdefault(cells[0], []).append(
                {
                    "name": cells[2][:500] if len(cells) > 2 else "",
                    "status": cells[1][:100],
                    "category": cells[3][:500] if len(cells) > 3 else None,
                }
            )
    seen = set()
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0]:
            continue
        target = public_target(cells[0])
        if not target:
            counts["unsupported_locator_skipped"] += 1
            continue
        kind, locator = target
        counts["rows"] += 1
        counts[kind] += 1
        if cells[0] in seen:
            continue
        seen.add(cells[0])
        statuses = sorted({a["status"] for a in assertions[cells[0]]})
        source_id = (
            f"telegram-directory:deepdarkcti:{digest[:16]}:" + hashlib.sha256(cells[0].encode()).hexdigest()[:16]
        )
        name = cells[2][:500] if len(cells) > 2 and cells[2] else locator
        store.register(
            source_id,
            "telegram_directory",
            cells[0],
            name,
            {
                "dataset": "deepdarkcti",
                "dataset_version": version,
                "collection": collection,
                "artifact_sha256": digest,
                "locator_type": kind,
                "locator": locator,
                "reported_status": statuses[0] if len(statuses) == 1 else "MULTIPLE_UPSTREAM_STATUSES",
                "reported_statuses": statuses,
                "reported_labels": assertions[cells[0]],
                "reported_category": cells[3][:500] if len(cells) > 3 else None,
                "upstream_url": f"{PROVIDERS['deepdarkcti']['url']}/blob/{version}/{collection}.md",
                "license": "GPL-3.0",
                "license_basis": "upstream_repository_license",
                "live_access_verified": False,
                "collection_enabled": False,
                "last_imported_at": imported_at,
                "coverage": COVERAGE,
            },
        )
        counts["unique_leads"] += 1
    if not counts["rows"]:
        raise DataAccessError("invalid_telegram_directory", "No supported Telegram locators found")
    return {
        "dataset": "deepdarkcti",
        "artifact_sha256": digest,
        "counts": dict(counts),
        "network_read": False,
        "coverage": COVERAGE,
    }


def list_open_sources(store, kind=None, dataset=None, query=None, offset=0, limit=20) -> dict:
    limit_value(limit)
    if kind not in {None, "telegram_archive", "telegram_directory"} or dataset not in {None, *PROVIDERS} or offset < 0:
        raise ValueError("Check kind, dataset and pagination")
    if query is not None and (not query.strip() or len(query) > 500):
        raise ValueError("Use a nonempty query up to 500 characters")
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,kind,locator,name,metadata FROM sources "
            "WHERE kind IN ('telegram_archive','telegram_directory') ORDER BY id"
        ).fetchall()
    results = []
    for row in rows:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        if (
            (kind is None or row["kind"] == kind)
            and (dataset is None or result["metadata"]["dataset"] == dataset)
            and (query is None or query.strip().casefold() in json.dumps(result, ensure_ascii=False).casefold())
        ):
            results.append(result)
    return {
        "results": results[offset : offset + limit],
        "total": len(results),
        "next_offset": offset + limit if offset + limit < len(results) else None,
        "coverage": COVERAGE,
    }
