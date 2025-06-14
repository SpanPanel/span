#!/usr/bin/env python

"""Run mypy with Home Assistant core path configuration."""

import subprocess  # nosec B404
import sys


def main() -> None:
    """Run mypy with Home Assistant core path configuration."""
    # Run mypy with the provided arguments
    result = subprocess.check_call(["poetry", "run", "mypy"] + sys.argv[1:])  # nosec B603
    sys.exit(result)


if __name__ == "__main__":
    main()
