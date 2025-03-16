#!/usr/bin/env python
import os
import subprocess  # nosec B404
import sys


def main() -> None:
    """Run mypy with Home Assistant core path configuration."""
    ha_core_path = os.environ.get("HA_CORE_PATH")
    if not ha_core_path:
        print(
            "Error: HA_CORE_PATH is not set. Please set it to your Home Assistant core directory.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        # Run mypy with the provided arguments
        result = subprocess.check_call(["poetry", "run", "mypy"] + sys.argv[1:])  # nosec B603
        sys.exit(result)


if __name__ == "__main__":
    main()
