"""Cross-process guard for writes to one StarTrail UserData directory."""

from contextlib import contextmanager
from pathlib import Path

import msvcrt


class UpdateBusyError(RuntimeError):
    """Another update or AI job is using this UserData directory."""


@contextmanager
def update_lock(data_dir: Path):
    path = Path(data_dir) / ".radar-update.lock"
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise UpdateBusyError("已有任务正在运行") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
