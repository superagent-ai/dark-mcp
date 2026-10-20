"""Fetch pinned upstreams and generate private local deployment configuration."""

import configparser
import json
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    manifest = json.loads((ROOT / "runtime/upstream.json").read_text())
    source = ROOT / ".runtime/ail-framework"
    if not source.exists():
        subprocess.run(["git", "clone", manifest["repository"], str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--detach", manifest["commit"]], check=True)
    subprocess.run(["git", "-C", str(source), "submodule", "update", "--init", "--recursive"], check=True)
    lacus = ROOT / ".runtime/lacus"
    if not lacus.exists():
        subprocess.run(["git", "clone", manifest["lacus"]["repository"], str(lacus)], check=True)
    subprocess.run(["git", "-C", str(lacus), "checkout", "--detach", manifest["lacus"]["commit"]], check=True)
    private = ROOT / "data/runtime"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = private / "secrets.json"
    if credentials.exists():
        tokens = json.loads(credentials.read_text())
    else:
        tokens = {
            name: secrets.token_hex(32)
            for name in (
                "meili",
                "master",
                "db",
                "dup",
                "cor",
                "crawl",
                "lg",
                "obj",
                "isim",
                "rel",
                "se",
                "tl",
                "stat",
                "tag",
                "track",
            )
        }
        credentials.write_text(json.dumps(tokens))
        credentials.chmod(0o600)
    cfg = configparser.ConfigParser()
    cfg.read(source / "configs/core.cfg.sample")
    namespaces = {
        "DB": "db",
        "Duplicates": "dup",
        "Correlations": "cor",
        "Crawler": "crawl",
        "Languages": "lg",
        "Objects": "obj",
        "ImageSimilarity": "isim",
        "Relationships": "rel",
        "Searchs": "se",
        "Timeline": "tl",
        "Stats": "stat",
        "Tags": "tag",
        "Trackers": "track",
    }
    for section, namespace in namespaces.items():
        cfg[f"Kvrocks_{section}"].update(host="kvrocks", port="6666", password=tokens[namespace])
    for section in cfg.sections():
        if section.startswith("Redis"):
            port = cfg.get(section, "port")
            cfg[section].update(
                host={"6379": "redis-cache", "6380": "redis-log", "6381": "redis-queues"}[port], port="6379"
            )
    for field in (
        "pastes",
        "hash",
        "crawled",
        "har",
        "files",
        "translated_pdf",
        "screenshot",
        "cookiejar_local_storage",
        "images",
        "favicons",
    ):
        cfg["Directories"][field] = "/data/objects/" + cfg["Directories"][field]
    cfg["Flask"]["host"] = "0.0.0.0"
    cfg["Notifications"]["ail_domain"] = "http://127.0.0.1:7000"
    cfg["Images"]["ollama_enabled"] = "False"
    cfg["Crawler"].update(default_har="False", default_screenshot="False", activate_crawler="False")
    cfg["Indexer"].update(
        meilisearch="True", meilisearch_url="http://meilisearch:7700", meilisearch_key=tokens["meili"]
    )
    with (private / "core.cfg").open("w") as handle:
        cfg.write(handle)
    (private / "core.cfg").chmod(0o600)
    kv = (
        "bind 0.0.0.0\nport 6666\ndir /data\nlog-dir /data\npidfile /data/kvrocks.pid\ndaemonize no\nrequirepass "
        + tokens["master"]
        + "\nrocksdb.block_cache_size 256\n"
    )
    kv += "".join(f"namespace.{name} {tokens[name]}\n" for name in namespaces.values())
    (private / "kvrocks.conf").write_text(kv)
    (private / "kvrocks.conf").chmod(0o600)
    (private / "meili.env").write_text(
        "MEILI_MASTER_KEY=" + tokens["meili"] + "\nMEILI_ENV=production\nMEILI_NO_ANALYTICS=true\n"
        "MEILI_MAX_INDEXING_MEMORY=512Mb\nMEILI_MAX_INDEXING_THREADS=2\n"
    )
    (private / "meili.env").chmod(0o600)
    (ROOT / "data/ail").mkdir(parents=True, exist_ok=True, mode=0o700)
    print("Pinned AIL source and private runtime configuration prepared.")


if __name__ == "__main__":
    prepare()
