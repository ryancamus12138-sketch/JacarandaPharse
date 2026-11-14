"""Entry point of the packaged backend (PyInstaller): the same commands as `jacara`. The desktop app
runs `jacara-backend desktop`; the launcher starts its workers as `jacara-backend worker ...` with
this same executable (desktop.self_command)."""

from jacara.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
