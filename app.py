import sys

from config import load_config
from ui import launch_app


def main() -> None:
    config = load_config()
    try:
        raise SystemExit(launch_app(config))
    except Exception as exc:
        print(f"PyQt6 app failed to start: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
