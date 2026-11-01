"""命令行入口：`jacara`。"""

import argparse

from jacara import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jacara", description="JacarandaParse 命令行工具")
    parser.add_argument("--version", action="version", version=f"jacara {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
