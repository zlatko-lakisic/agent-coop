"""Print progress lines for a fake long-running project script.

It touches nothing except standard output.
"""

from __future__ import annotations

import argparse
import time


def main(argv: list[str] | None = None) -> int:
    """Print `epoch N of M` and exit."""
    parser = argparse.ArgumentParser(description="Print fake training progress.")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    epochs = args.epochs if args.epochs > 0 else 1
    for number in range(1, epochs + 1):
        print("epoch " + str(number) + " of " + str(epochs), flush=True)
        if number != epochs and args.interval > 0:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
