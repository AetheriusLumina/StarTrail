"""Small, best-effort error log kept beside the user's database."""

import os
from datetime import datetime
from pathlib import Path


def record_error(data_dir: Path, category: str, detail: str) -> None:
    path = Path(data_dir) / "radar.log"
    line = (f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"[{category}] {detail.replace(chr(10), ' ').replace(chr(13), ' ')[:500]}\n")
    try:
        if path.exists() and path.stat().st_size > 128_000:
            os.replace(path, path.with_name("radar.log.1"))
        with path.open("a", encoding="utf-8") as output:
            output.write(line)
    except OSError:
        # Logging must never turn a recoverable update failure into a crash.
        pass
