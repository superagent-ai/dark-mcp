# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bootstrap and supervise genuine upstream AIL processes; no replacement data engine."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from lib.ConfigLoader import ConfigLoader


def main():
    for directory in ("/data/logs", "/opt/ail/temp", "/opt/ail/var/www/submitted"):
        Path(directory).mkdir(parents=True, exist_ok=True)
    if not Path("/opt/ail/logs").exists():
        Path("/opt/ail/logs").symlink_to("/data/logs", target_is_directory=True)
    cfg = ConfigLoader()
    for section in cfg.get_config_sections():
        if section.startswith(("Redis", "Kvrocks")):
            conn = cfg.get_redis_conn(section) if section.startswith("Redis") else cfg.get_db_conn(section)
            for attempt in range(60):
                try:
                    conn.ping()
                    break
                except Exception:
                    if attempt == 59:
                        raise
                    time.sleep(1)
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
        Path(cfg.get_files_directory(field)).mkdir(parents=True, exist_ok=True)
    from lib import ail_orgs, ail_users, crawlers

    ail_users._create_roles()
    user = "admin@admin.test"
    if not ail_users.exists_user(user):
        password = ail_users.gen_password()
        org = ail_orgs.create_default_org()
        ail_users.create_user(user, password=password, admin_id=user, org_uuid=org.get_uuid(), role="admin")
        target = Path("/data/admin.json")
        target.write_text(json.dumps({"user": user, "password": password}))
        target.chmod(0o600)
    target = Path("/data/api-key")
    target.write_text(ail_users.get_default_admin_token())
    target.chmod(0o600)
    crawlers.api_save_lacus_url_key({"url": "http://lacus:7100", "api_key": ""})
    crawlers.save_nb_max_captures(1)
    subprocess.run([sys.executable, "/opt/ail/bin/AIL_Init.py"], check=True)
    workers = [
        "importer/FeederImporter.py",
        "importer/CrawlerImporter.py",
        "modules/Mixer.py",
        "modules/Global.py",
        "modules/Indexer.py",
        "modules/Categ.py",
        "modules/Urls.py",
        "modules/Onion.py",
        "modules/Tags.py",
        "crawlers/Crawler.py",
    ]
    children = []
    for worker in workers:
        children.append(subprocess.Popen([sys.executable, f"/opt/ail/bin/{worker}"], cwd="/opt/ail/bin"))
    children.append(
        subprocess.Popen(["gunicorn", "--bind", "0.0.0.0:7000", "--workers", "1", "--threads", "4", "ail_api_app:app"])
    )

    def stop(*_):
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    try:
        while True:
            for child, name in zip(children, [*workers, "API"], strict=True):
                if child.poll() is not None:
                    raise RuntimeError(f"AIL process exited: {name}, status={child.returncode}")
            time.sleep(1)
    finally:
        stop()


if __name__ == "__main__":
    os.umask(0o077)
    main()
