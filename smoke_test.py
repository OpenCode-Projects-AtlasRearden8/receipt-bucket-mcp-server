#!/usr/bin/env python3
"""Smoke test for the receipt-bucket MCP server.

Runs the real server as a subprocess and has a short, literal JSON-RPC
conversation with it over stdin/stdout — the same lines an MCP client
would send. All of it is offline; nothing leaves your machine.

Usage:
    python3 smoke_test.py

Exit code 0 means every step passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run_step(label: str, ok: bool, detail: str = "") -> bool:
    check = "PASS" if ok else "FAIL"
    tail = f" — {detail}" if detail else ""
    print(f"[{check}] {label}{tail}")
    return ok


def send(message: dict) -> str:
    return json.dumps(message) + "\n"


def main() -> int:
    all_ok = True
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "receipt_bucket",
                "--data-dir",
                tmp,
                "--bucket",
                "smoke",
            ],
            input=(
                send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-test", "version": "0"}}})
                + send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                + send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                + send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "add_receipt", "arguments": {"amount": 12.34, "merchant": "Corner Coffee Co.", "category": "meals", "date": "2026-10-24", "tags": ["coffee"], "note": "written just now by the smoke test"}}})
                + send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "search_receipts", "arguments": {"merchant": "corner"}}})
                + send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "monthly_summary", "arguments": {"month": "2026-10"}}})
                + send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "export_csv", "arguments": {}}})
                + send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "bucket_info", "arguments": {}}})
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        replies = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
        by_id = {r.get("id"): r for r in replies}

        hello = by_id.get(1, {}).get("result", {})
        all_ok &= run_step(
            "initialize handshake",
            hello.get("serverInfo", {}).get("name") == "receipt-bucket"
            and hello.get("protocolVersion") == "2025-06-18",
            hello.get("serverInfo", {}).get("version", ""),
        )

        names = {t["name"] for t in by_id.get(2, {}).get("result", {}).get("tools", [])}
        all_ok &= run_step(
            "tools/list advertises 7 tools",
            names == {"add_receipt", "search_receipts", "tag_receipt", "delete_receipt",
                      "monthly_summary", "export_csv", "bucket_info"},
            ", ".join(sorted(names)),
        )

        added = json.loads(by_id[3]["result"]["content"][0]["text"])
        all_ok &= run_step(
            "add_receipt stored the receipt",
            added["added"]["amount"] == 12.34,
            added["added"]["id"],
        )

        found = json.loads(by_id[4]["result"]["content"][0]["text"])
        all_ok &= run_step(
            "search_receipts found it",
            found["match_count"] == 1 and found["matching_total"] == {"USD": 12.34},
            f"{found['match_count']} match(es), total {found['matching_total'].get('USD')}",
        )

        summary = json.loads(by_id[5]["result"]["content"][0]["text"])
        all_ok &= run_step(
            "monthly_summary cents-exact",
            summary["overall"]["total"] == {"USD": 12.34},
            f"{len(summary['months'])} month(s) summarized",
        )

        export = json.loads(by_id[6]["result"]["content"][0]["text"])
        ok_export = (
            export["row_count"] == 1
            and "2026-10-24,Corner Coffee Co. ,-12.34" not in export["csv"]
            and "2026-10-24,Corner Coffee Co.,-12.34,USD,out,meals" in export["csv"]
        )
        all_ok &= run_step("export_csv has bookkeeping-ready row", ok_export, export["csv"].splitlines()[1])

        info = json.loads(by_id[7]["result"]["content"][0]["text"])
        all_ok &= run_step(
            "bucket_info reports local storage",
            info["receipt_count"] == 1 and info["total_all_time"] == {"USD": 12.34},
            info["storage_file"],
        )

        all_ok &= run_step(
            "server exited cleanly at EOF",
            proc.returncode == 0,
            f"exit {proc.returncode}",
        )
    print()
    print("Smoke test " + ("PASSED — the bucket is ready." if all_ok else "FAILED — see lines above."))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
