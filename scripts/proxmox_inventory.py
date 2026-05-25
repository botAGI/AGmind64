#!/usr/bin/env python3
"""Convert `tofu output -json` into an AGmind Ansible inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agmind.deploy.proxmox_inventory import (  # noqa: E402
    DEFAULT_INVENTORY_PATH,
    ProxmoxInventoryError,
    load_tofu_output_json,
    write_inventory_from_tofu_outputs,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to JSON captured from `tofu output -json`.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_INVENTORY_PATH,
        help=f"Inventory YAML path to write (default: {DEFAULT_INVENTORY_PATH}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        outputs = load_tofu_output_json(args.input)
        written = write_inventory_from_tofu_outputs(outputs, args.output)
    except ProxmoxInventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
