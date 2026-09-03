# SPDX-License-Identifier: Apache-2.0
"""Allow `python -m whence` for anyone who has not put the script on PATH."""

from .cli import main

if __name__ == "__main__":
    main()
