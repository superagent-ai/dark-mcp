import pytest

from dark_mcp.index import OnionIndex
from dark_mcp.models import OnionPage


def test_reindex_replaces_old_content_and_dates_are_utc(settings, onion):
    index = OnionIndex(settings.prepare_data_dir())
    index.put(OnionPage(url=onion, title="First", text="Old claim"))
    index.put(OnionPage(url=onion, title="Latest", text="Acme exposure", collected_at="2026-10-18T10:00:00+00:00"))
    assert index.count() == 1
    assert not index.search("Old")["results"]
    assert index.search("Acme", since="2026-10-18T11:00:00+02:00")["results"]
    assert not index.search("Acme", since="2026-10-18T11:00:00Z")["results"]
    assert index.search("Acme")["coverage"]["scope"] == "locally_indexed_pages"


def test_queries_cannot_inject_fts_or_sql(settings):
    index = OnionIndex(settings.prepare_data_dir())
    assert index.search('" OR 1=1; DROP TABLE pages; --')["results"] == []
    assert index.count() == 0
    with pytest.raises(ValueError):
        index.search("*")
    with pytest.raises(ValueError):
        index.search("Acme", since="2026-10-18")
