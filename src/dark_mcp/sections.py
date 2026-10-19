"""Bounded planning for native forum section ingestion; no scheduling or network calls."""

from urllib.parse import urlencode, urlsplit, urlunsplit

from dark_mcp.forums import page_number, source_url, thread_identity
from dark_mcp.models import now


def thread_root(source: dict, url: str) -> str:
    url = source_url(source, url)
    parts = urlsplit(url)
    key = thread_identity(url)
    if key is None:
        raise ValueError("Expected a discovered thread URL")
    if key.startswith("q2a:"):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    if key.startswith("tid:"):
        return urlunsplit((parts.scheme, parts.netloc, "/showthread.php", urlencode({"tid": key[4:]}), ""))
    return urlunsplit((parts.scheme, parts.netloc, key + ("/" if key.startswith("/threads/") else ""), "", ""))


class SectionPlan:
    def __init__(self, metadata: dict, options: dict, checkpoint: dict):
        self.source, self.options, self.checkpoint = metadata, options, checkpoint
        self.state = checkpoint.setdefault(
            "section",
            {
                "index_pages": 0,
                "index_rows_omitted": 0,
                "threads_selected": [],
                "thread_pages": {},
                "thread_owners": {},
                "failed_pages": [],
                "deferred_urls": [],
                "hidden_posts_seen": 0,
                "deferred_count": 0,
            },
        )

    def deferred(self, url: str, reason: str):
        self.state["deferred_count"] += 1
        if len(self.state["deferred_urls"]) < 100:
            self.state["deferred_urls"].append({"url": url, "reason": reason})

    def failure(self, url: str, error: dict):
        self.state["failed_pages"].append({"url": url, "error": error, "attempted_at": now()})
        self.checkpoint["queue"].pop(0)
        owner = self.state["thread_owners"].pop(url, None)
        if self.options.get("thread_page_order") == "root_then_last" and owner:
            sample = self.state.setdefault("thread_samples", {}).setdefault(owner, {})
            sample.update(state="sample_page_failed", failed_url=url, last_page_collected=False)
        self.checkpoint["visited"].append(url)

    def advance(self, page):
        queue, visited = self.checkpoint["queue"], self.checkpoint["visited"]
        requested = queue.pop(0)
        owner = self.state["thread_owners"].pop(requested, None)
        visited.extend(u for u in [page.url] if u not in visited)
        self.state["hidden_posts_seen"] += sum(p.hidden_content for p in page.posts)
        future = []
        if page.page_type == "index":
            self.state["index_pages"] += 1
            self.state["index_rows_omitted"] = self.state.get("index_rows_omitted", 0) + page.omitted_threads
            for row in page.threads:
                term = self.options.get("query")
                if term and term.casefold().strip() not in row["title"].casefold():
                    continue
                root = thread_root(self.source, row["url"])
                if self.options.get("exclude_pinned") and row.get("pinned") is True:
                    self.deferred(root, "pinned_thread_excluded")
                    continue
                if root in self.state["threads_selected"]:
                    continue
                if len(self.state["threads_selected"]) >= self.options["max_threads"]:
                    self.deferred(root, "thread_selection_bound")
                    continue
                self.state["threads_selected"].append(root)
                future.append(root)
                self.state["thread_owners"][root] = root
            for url in page.next_page_urls:
                if self.state["index_pages"] < self.options["max_index_pages"]:
                    future.append(url)
                else:
                    self.deferred(url, "index_page_bound")
        else:
            root = owner or thread_root(self.source, page.url)
            number = self.state["thread_pages"].get(root, 0) + 1
            self.state["thread_pages"][root] = number
            if self.options.get("thread_page_order") == "root_then_last":
                from dark_mcp.thread_context import forum_thread_key

                samples = self.state.setdefault("thread_samples", {})
                sample = samples.setdefault(root, {"root_requested": False, "last_page_collected": False})
                if forum_thread_key(page.url) != forum_thread_key(root):
                    sample.update(state="thread_identity_changed", returned_url=page.url)
                elif number == 1 and page_number(page.url) != 1:
                    sample.update(state="root_page_changed", root_requested=True, returned_url=page.url)
                elif number == 1:
                    sample.update(
                        root_requested=True,
                        root_observed=any(post.is_thread_root is True for post in page.posts),
                        last_page_url=page.last_page_url,
                        last_page_number=page.last_page_number,
                        last_page_basis=page.last_page_basis,
                        last_page_conflict=page.last_page_conflict,
                    )
                    if page.last_page_url and page.last_page_number and page.last_page_number > page_number(page.url):
                        future.append(page.last_page_url)
                        self.state["thread_owners"][page.last_page_url] = root
                        sample["state"] = "last_page_queued"
                        if page.last_page_number > 2:
                            sample["uncollected_middle_pages"] = {"from": 2, "through": page.last_page_number - 1}
                            for pending in page.next_page_urls:
                                if pending != page.last_page_url:
                                    self.deferred(pending, "middle_pages_not_sampled")
                    else:
                        sample["state"] = (
                            "last_page_conflict"
                            if page.last_page_conflict
                            else ("last_page_not_explicit" if page.next_page_urls else "no_forward_pagination_observed")
                        )
                        for pending in page.next_page_urls:
                            self.deferred(pending, "last_page_not_explicit")
                        if page.last_page_url and page.last_page_number == page_number(page.url):
                            sample.update(state="single_sample_is_declared_last", last_page_collected=True)
                else:
                    matched = page_number(page.url) == sample.get("last_page_number")
                    sample.update(
                        last_page_collected=matched,
                        state="root_and_last_sampled" if matched else "sample_page_changed",
                        sampled_last_url=page.url,
                        sampled_page_number=page_number(page.url),
                        last_page_number_on_sampled_page=page.last_page_number,
                        sampled_last_page_conflict=page.last_page_conflict,
                    )
                    # A thread can grow between requests. Preserve the observed boundary,
                    # without extending this bounded sample into a continuous collector.
                    sample["later_pagination_observed"] = bool(page.next_page_urls)
                    for pending in page.next_page_urls:
                        self.deferred(pending, "pagination_after_sample_boundary")
                queue.extend(u for u in future if u not in visited and u not in queue)
                self.state["thread_owners"] = {
                    u: owner for u, owner in self.state["thread_owners"].items() if u in queue
                }
                return
            for url in page.next_page_urls:
                if number < self.options["max_thread_pages"]:
                    future.append(url)
                    self.state["thread_owners"][url] = root
                else:
                    self.deferred(url, "thread_page_bound")
        queue.extend(u for u in future if u not in visited and u not in queue)
        self.state["thread_owners"] = {u: owner for u, owner in self.state["thread_owners"].items() if u in queue}
