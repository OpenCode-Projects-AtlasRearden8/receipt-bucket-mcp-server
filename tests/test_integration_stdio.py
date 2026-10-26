"""End-to-end test: spawn the real server process and speak MCP over stdio pipes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def line(message: dict) -> str:
    return json.dumps(message) + "\n"


class TestStdioServerProcess(unittest.TestCase):
    def run_session(self, messages):
        with tempfile.TemporaryDirectory() as tmp:
            lines = [line(m) if isinstance(m, dict) else m for m in messages]
            proc = subprocess.run(
                [sys.executable, "-m", "receipt_bucket", "--data-dir", tmp, "--bucket", "e2e"],
                input="".join(lines),
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(REPO_ROOT),
            )
        return proc

    def test_full_session_round_trip(self):
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "tests", "version": "0"}}}
        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        tools_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        add = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "add_receipt", "arguments": {"amount": "12.34", "merchant": "Corner Cafe", "date": "2026-10-24", "category": "meals", "tags": ["coffee"]}}}
        search = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "search_receipts", "arguments": {"merchant": "corner"}}}
        summary = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                   "params": {"name": "monthly_summary", "arguments": {"month": "2026-10"}}}
        export = {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "export_csv", "arguments": {}}}
        info = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "bucket_info", "arguments": {}}}
        bogus = {"jsonrpc": "2.0", "id": 8, "method": "prompts/list"}
        proc = self.run_session([initialize, initialized, tools_list, add, search, summary, export, info, bogus])

        payload_lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(payload_lines), 8)  # nothing for notifications; one per request
        replies = [json.loads(l) for l in payload_lines]
        by_id = {reply.get("id"): reply for reply in replies}

        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        tool_names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertIn("add_receipt", tool_names)

        added = json.loads(by_id[3]["result"]["content"][0]["text"])
        receipt_id = added["added"]["id"]
        self.assertTrue(receipt_id.startswith("rcpt-"))

        found = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertEqual(found["match_count"], 1)
        self.assertEqual(found["receipts"][0]["amount"], 12.34)

        summary_payload = json.loads(by_id[5]["result"]["content"][0]["text"])
        self.assertEqual(summary_payload["overall"]["total"], {"USD": 12.34})

        exported = json.loads(by_id[6]["result"]["content"][0]["text"])
        self.assertEqual(exported["row_count"], 1)
        self.assertIn("2026-10-24,Corner Cafe,-12.34,USD,out,meals", exported["csv"])

        info_payload = json.loads(by_id[7]["result"]["content"][0]["text"])
        self.assertEqual(info_payload["receipt_count"], 1)
        self.assertIn("backups", info_payload)

        self.assertEqual(by_id[8]["error"]["code"], -32601)
        self.assertIn("Method not found", by_id[8]["error"]["message"])

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_parse_error_line_reported(self):
        proc = self.run_session(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                "{not json\n",
                {"jsonrpc": "2.0", "method": "unknown-notification-style"},
            ]
        )
        replies = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
        parse_errors = [r for r in replies if r.get("id") is None and r.get("error", {}).get("code") == -32700]
        self.assertEqual(len(parse_errors), 1)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_stderr_has_no_protocol_noise(self):
        proc = self.run_session([{"jsonrpc": "2.0", "id": 1, "method": "initialize"}])
        self.assertNotIn('"jsonrpc"', proc.stderr)


if __name__ == "__main__":
    unittest.main()
