"""Launch upstream Lacus API and capture manager with local Redis and Tor."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path


def main():
    for sample in Path("config").glob("*.json.sample"):
        target = sample.with_suffix("")
        if not target.exists():
            target.write_bytes(sample.read_bytes())
    target = Path("config/generic.json")
    config = json.loads(target.read_text())
    config.update(
        concurrent_captures=1, max_capture_time=180, max_retries=1, only_global_lookups=True, allow_headed=False
    )
    target.write_text(json.dumps(config))
    Path("logs").mkdir(exist_ok=True)
    Path("/data/tor").mkdir(parents=True, exist_ok=True)
    children = [
        subprocess.Popen(
            [
                "redis-server",
                "--port",
                "0",
                "--unixsocket",
                "/opt/lacus/cache/cache.sock",
                "--dir",
                "/data",
                "--appendonly",
                "yes",
            ]
        ),
        subprocess.Popen(["tor", "--SocksPort", "127.0.0.1:9050", "--DataDirectory", "/data/tor"]),
    ]
    for _ in range(60):
        if Path("cache/cache.sock").exists():
            break
        time.sleep(1)
    children.extend(
        [
            subprocess.Popen([sys.executable, "/opt/lacus/bin/capture_manager.py"]),
            subprocess.Popen(
                [
                    "gunicorn",
                    "--bind",
                    "0.0.0.0:7100",
                    "--workers",
                    "1",
                    "--threads",
                    "2",
                    "--timeout",
                    "200",
                    "web:app",
                ],
                cwd="website",
            ),
        ]
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A Lacus service exited; inspect container logs")
            time.sleep(1)
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
