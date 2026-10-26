"""Command-line entry point: ``python -m receipt_bucket``."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .errors import ToolError
from .server import dispatch
from .storage import ReceiptStore
from .validation import validate_bucket_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="receipt-bucket",
        description=(
            "A small local-first MCP server (stdio) that stores receipts in JSON, "
            "summarizes monthly spend, and exports a bookkeeping-ready CSV."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("RECEIPTS_DATA_DIR", "./data"),
        help="Directory holding bucket files and backups (default: ./data or $RECEIPTS_DATA_DIR).",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("RECEIPTS_BUCKET", "receipts"),
        help="Bucket name, one JSON file per bucket (default: 'receipts').",
    )
    parser.add_argument(
        "--currency",
        default=os.environ.get("RECEIPTS_CURRENCY", "USD"),
        help="Default 3-letter currency code for new receipts (default: USD).",
    )
    parser.add_argument(
        "--keep-backups",
        type=int,
        default=10,
        help="How many automatic backups to keep per bucket (default: 10).",
    )
    parser.add_argument("--version", action="version", version=f"receipt-bucket {__version__}")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_bucket_name(args.bucket)
        store = ReceiptStore(
            args.data_dir,
            args.bucket,
            currency=args.currency.strip().upper() if args.currency else "USD",
            keep_backups=args.keep_backups,
        )
    except ToolError as exc:
        print(f"receipt-bucket: cannot start server: {exc}", file=sys.stderr)
        print(
            "Hint: run like  python3 -m receipt_bucket --data-dir ./data --bucket receipts",
            file=sys.stderr,
        )
        return 2

    print(
        f"receipt-bucket: serving '{args.bucket}' from {store.path} on stdio (Ctrl+C to stop).",
        file=sys.stderr,
    )

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                print(
                    '{"jsonrpc": "2.0", "id": null, "error": {"code": -32700, '
                    '"message": "Parse error: request line was not valid JSON"}}',
                    flush=True,
                )
                continue
            reply = dispatch(store, message)
            if reply is not None:
                print(reply, flush=True)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    print("receipt-bucket: bye.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
