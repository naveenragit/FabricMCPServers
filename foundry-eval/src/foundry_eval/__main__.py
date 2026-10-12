"""Regenerate the JSON Schema files: python -m foundry_eval schemas <directory>."""

from __future__ import annotations

import sys

from .store import write_json_schemas


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "schemas":
        print("usage: python -m foundry_eval schemas <directory>", file=sys.stderr)
        return 2
    for path in write_json_schemas(args[1]):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
