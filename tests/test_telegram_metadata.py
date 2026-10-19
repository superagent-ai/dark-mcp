from datetime import UTC, datetime
from types import SimpleNamespace

from mcp import Client
from telethon import types

from dark_mcp.evidence import EvidenceStore
from dark_mcp.references import ReferenceSearch
from dark_mcp.server import create_server
from dark_mcp.telegram import TelegramConnector
from dark_mcp.telegram_metadata import forward_attribution, message_links


def example_message():
    return SimpleNamespace(
        id=42,
        date=datetime(2026, 10, 19, tzinfo=UTC),
        message="🔎 Details https://relay.example/item",
        peer_id=types.PeerChannel(123),
        media=None,
        entities=[
            types.MessageEntityTextUrl(3, 7, "https://company.example/source"),
            types.MessageEntityUrl(11, 26),
        ],
        fwd_from=types.MessageFwdHeader(
            date=datetime(2026, 10, 17, tzinfo=UTC),
            from_id=types.PeerChannel(456),
            channel_post=9,
            saved_from_peer=types.PeerChannel(789),
            saved_from_msg_id=88,
            saved_date=datetime(2026, 10, 18, tzinfo=UTC),
        ),
    )


def example_channel():
    return types.Channel(
        id=123,
        title="Relay fixture",
        photo=types.ChatPhotoEmpty(),
        date=datetime(2026, 10, 19, tzinfo=UTC),
        broadcast=True,
        username="relayfixture",
    )


def test_hidden_link_offsets_account_for_emoji_and_forward_dates_are_distinct():
    post = TelegramConnector._message(example_message(), example_channel())
    assert [link["url"] for link in post["links"]] == [
        "https://company.example/source",
        "https://relay.example/item",
    ]
    assert post["links"][0]["label"] == "Details"
    assert post["links"][0]["offset"] == 3 and post["links"][0]["offset_unit"] == "utf16_code_units"
    assert post["published_at"].startswith("2026-10-19")
    forward = post["forward"]
    assert forward["origin_published_at"].startswith("2026-10-17")
    assert forward["saved_context"]["date"].startswith("2026-10-18")
    assert forward["origin_peer"] == {"kind": "channel", "id": 456}
    assert forward["saved_context"]["peer"] == {"kind": "channel", "id": 789}
    assert forward["origin_url"] == "https://t.me/c/456/9"
    assert not forward["origin_verified"] and not forward["origin_fetched"]
    assert post["channel"]["id"] == "-100123" and post["url"] == "https://t.me/relayfixture/42"


def test_forward_privacy_imports_and_saved_context_do_not_create_false_origins():
    header = types.MessageFwdHeader(
        date=datetime(2026, 10, 17, tzinfo=UTC),
        from_name="Anonymous " * 100,
        saved_from_peer=types.PeerChannel(789),
        saved_from_msg_id=88,
    )
    hidden = forward_attribution(header)
    assert hidden["origin_peer"] is None and hidden["origin_url"] is None
    assert len(hidden["origin_name"]) == 500
    header.from_id = types.PeerUser(321)
    header.channel_post = 9
    assert forward_attribution(header)["origin_url"] is None
    header.from_id = types.PeerChannel(456)
    header.imported = True
    assert forward_attribution(header)["origin_url"] is None
    assert forward_attribution(header)["imported"]
    assert forward_attribution(None) is None


def test_bad_entity_ranges_surrogate_splits_and_unsupported_destinations_are_omitted():
    entities = [
        types.MessageEntityUrl(-1, 3),
        types.MessageEntityUrl(0, 1),  # Half of the emoji.
        types.MessageEntityUrl(1, 1),
        types.MessageEntityUrl(3, 500),
        types.MessageEntityTextUrl(3, 7, "javascript:alert(1)"),
        types.MessageEntityTextUrl(3, 7, "https://user:secret@example.com/"),
        types.MessageEntityTextUrl(3, 7, "https://company.example/\nsecret"),
        types.MessageEntityBold(3, 7),
        types.MessageEntityTextUrl(3, 7, "https://company.example/valid"),
    ]
    links, capture = message_links("🔎 Details", entities)
    assert len(links) == 1 and links[0]["label"] == "Details"
    assert capture["url_entities_omitted"] == 7 and capture["entities_examined"] == 9
    assert not capture["entities_truncated"]


def test_entity_and_retained_text_bounds_are_reported_without_inventing_plaintext_links():
    text = "a" * 16000 + " https://omitted.example/"
    message = example_message()
    message.message = text
    message.entities = [types.MessageEntityBold(0, 1)] * 199 + [
        types.MessageEntityUrl(16001, 24),
        types.MessageEntityTextUrl(0, 1, "https://unexamined.example/"),
    ]
    post = TelegramConnector._message(message, example_channel())
    assert post["truncated"] and len(post["text"]) == 16000 and not post["links"]
    assert post["link_capture"]["entities_truncated"] and post["link_capture"]["url_entities_omitted"] == 1
    assert message_links("https://company.example/", None)[0] == []
    assert message_links("company.example", [types.MessageEntityUrl(0, 15)])[0] == []


async def test_mcp_collection_search_replay_restart_and_link_only_revision(settings, monkeypatch):
    message = example_message()
    calls = []

    async def messages(self, **kwargs):
        calls.append(kwargs)
        post = self._message(message, example_channel())
        return {"channel": post["channel"], "results": [post], "collected_at": "2026-10-19T01:00:00Z"}

    monkeypatch.setattr(TelegramConnector, "messages", messages)
    async with Client(create_server(settings)) as client:
        read = (await client.call_tool("read_telegram_channel", {"channel": "relayfixture"})).structured_content
        assert read["ok"]
        original = read["results"][0]["evidence"]
        assert len(calls) == 1
        body = (await client.call_tool("search_evidence", {"query": "company.example"})).structured_content
        assert not body["results"]
        refs = (
            await client.call_tool("search_references", {"host": "company.example", "kind": "telegram"})
        ).structured_content
        hit = refs["results"][0]
        assert hit["evidence_id"] == original["evidence_id"]
        assert hit["reference_metadata"]["basis"] == "telegram_text_url_entity"
        assert hit["forward"]["origin_url"] == "https://t.me/c/456/9"
        assert hit["source_url_basis"] == "selected_revision_snapshot"
        assert hit["acquisition"]["method"] == "telegram_mtproto"
        assert not refs["reference_followed"] and len(calls) == 1
        message.entities[0].url = "https://replacement.example/source"
        updated = (await client.call_tool("read_telegram_channel", {"channel": "relayfixture"})).structured_content
        current = updated["results"][0]["evidence"]
        assert current["revision_id"] != original["revision_id"]
        assert current["content_hash"] == original["content_hash"]
        assert current["snapshot_hash"] != original["snapshot_hash"]
    async with Client(create_server(settings)) as client:
        args = {"host": "company.example", "kind": "telegram"}
        assert not (await client.call_tool("search_references", args)).structured_content["results"]
        old = (await client.call_tool("search_references", {**args, "include_history": True})).structured_content[
            "results"
        ][0]
        assert old["revision_id"] == original["revision_id"] and not old["is_latest_revision"]
        exact = (
            await client.call_tool(
                "get_evidence",
                {
                    "evidence_id": old["evidence_id"],
                    "revision_id": old["revision_id"],
                },
            )
        ).structured_content["evidence"]
        assert exact["snapshot_hash"] == original["snapshot_hash"]
        assert exact["snapshot"]["link_capture"]["links_captured"] == 2
        assert exact["snapshot"]["forward"]["origin_peer"]["id"] == 456
        assert len(calls) == 2


def test_telegram_reference_migration_is_repeatable_and_does_not_enrich_legacy_snapshots(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("tg", "telegram", "relayfixture", "Relay fixture")
    captured = store.observe(
        "tg",
        "telegram",
        "1",
        "https://t.me/relayfixture/1",
        {
            "text": "Details",
            "links": [{"url": "https://company.example/", "label": "Details"}],
        },
    )
    legacy = store.observe("tg", "telegram", "2", "https://t.me/relayfixture/2", {"text": "https://legacy.example/"})
    counts = store.counts()
    with store.connect() as db:
        db.execute("DELETE FROM evidence_references")
        db.execute("INSERT INTO reference_search(reference_search) VALUES('rebuild')")
        db.execute("DELETE FROM migrations WHERE name='telegram-reference-index-v1'")
    for _ in range(2):
        reopened = EvidenceStore(settings.data_dir)
        assert reopened.counts() == counts
        hits = ReferenceSearch(reopened).search(host="company.example", kind="telegram")["results"]
        assert len(hits) == 1 and hits[0]["snapshot_hash"] == captured["snapshot_hash"]
        assert not ReferenceSearch(reopened).search(host="legacy.example")["results"]
        assert "links" not in reopened.get(legacy["evidence_id"])["snapshot"]
