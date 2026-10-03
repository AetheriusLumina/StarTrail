"""Resolve visible installation and personal-data locations."""

from pathlib import Path


def install_dir(executable: Path) -> Path:
    return executable.resolve().parent


def default_data_dir(package_file: Path, executable: Path, frozen: bool) -> Path:
    if frozen:
        return install_dir(executable) / "UserData"
    return package_file.resolve().parent.parent / "UserData"
