"""One PyInstaller payload, two clear top-level EXE names."""

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    if Path(sys.executable).stem.casefold() == "uninstall":
        from github_radar.uninstall import launch_uninstaller

        return launch_uninstaller(Path(sys.executable).resolve().parent)

    from github_radar.__main__ import main as radar_main

    return radar_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
