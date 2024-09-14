#!/usr/bin/env python
import os
import subprocess
import sys


def main():
    ha_core_path = os.environ.get('HA_CORE_PATH')
    if not ha_core_path:
        print("Error: HA_CORE_PATH is not set. Please set it to your Home Assistant core directory.", file=sys.stderr)
        sys.exit(1)
    else:
        # Run mypy with the provided arguments
        result = subprocess.run(['poetry', 'run', 'mypy'] + sys.argv[1:])
        sys.exit(result.returncode)

if __name__ == '__main__':
    main()
