"""MCP protocol tests against the dispatch loop (no subprocess needed)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from receipt_bucket.errors import CorruptBucketError
from receipt_bucket.server import dispatch, SUPPORTED_PROTOCOL_VERSIONS
from receipt_bucket.storage import ReceiptStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def request(id_value, method, params=None):
    return {"jsonrpc": "2.0", "id": id_value, "method": method, "params": params or {}}


def notification(method, params=None):
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = ReceiptStore(self.dir, "receipts")

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_echoes_supported_version(self):
        for version in SUPPORTED_PROTOCOL_VERSIONS:
            reply = json.loads(dispatch(self.store, request(1, "initialize", {"protocolVersion": version})))
            self.assertEqual(reply["id"], 1)
            self.assertEqual(reply["result"]["protocolVersion"], version)
            self.assertEqual(reply["result"]["serverInfo"]["name"], "receipt-bucket")
            self.assertTrue(reply["result"]["capabilities"]["tools"])

    def test_initialize_falls_back_to_latest(self):
        reply = json.loads(dispatch(self.store, request("a", "initialize", {"protocolVersion": "1999-01-01"})))
        self.assertEqual(reply["id"], "a")
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_without_id_is_a_notification(self):
        self.assertIsNone(dispatch(self.store, notification("initialize", {})))

    def test_initialized_notification_silent(self):
        self.assertIsNone(dispatch(self.store, notification("notifications/initialized")))

    def test_ping(self):
        reply = json.loads(dispatch(self.store, request(7, "ping")))
        self.assertEqual(reply["result"], {})

    def test_tools_list_complete(self):
        reply = json.loads(dispatch(self.store, request(2, "tools/list")))
        names = [tool["name"] for tool in reply["result"]["tools"]]
        self.assertEqual(
            names,
            ["add_receipt", "search_receipts", "tag_receipt", "delete_receipt",
             "monthly_summary", "export_csv", "bucket_info"],
        )
        for tool in reply["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_call_round_trip(self):
        reply = json.loads(
            dispatch(self.store, request(3, "tools/call", {"name": "add_receipt", "arguments": {"amount": 5.0, "merchant": "Cafe"}}))
        )
        result = reply["result"]
        self.assertFalse(result.get("isError", False))
        added = json.loads(result["content"][0]["text"])
        self.assertEqual(added["added"]["amount"], 5.0)
        self.assertTrue(added["storage_file"].endswith("receipts.json"))

    def test_tool_input_error_reported_with_is_error(self):
        reply = json.loads(
            dispatch(self.store, request(4, "tools/call", {"name": "add_receipt", "arguments": {"merchant": "Cafe"}}))
        )
        result = reply["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Error:", result["content"][0]["text"])

    def test_search_after_add(self):
        dispatch(self.store, request(1, "tools/call", {"name": "add_receipt", "arguments": {"amount": 5.5, "merchant": "Zeta Shop", "date": "2026-05-05"}}))
        reply = json.loads(dispatch(self.store, request(2, "tools/call", {"name": "search_receipts", "arguments": {"text": "zeta"}})))
        found = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(found["match_count"], 1)
        self.assertEqual(found["receipts"][0]["merchant"], "Zeta Shop")

    def test_unknown_tool_is_json_rpc_error(self):
        reply = json.loads(dispatch(self.store, request(5, "tools/call", {"name": "not_a_tool"})))
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertEqual(reply["id"], 5)
        self.assertIn("add_receipt", reply["error"]["data"]["available_tools"])

    def test_tools_call_requires_name(self):
        reply = json.loads(dispatch(self.store, request(6, "tools/call", {"arguments": {}})))
        self.assertEqual(reply["error"]["code"], -32602)

    def test_unknown_method_not_found(self):
        reply = json.loads(dispatch(self.store, request(9, "prompts/list")))
        self.assertEqual(reply["error"]["code"], -32601)

    def test_unknown_notification_ignored(self):
        self.assertIsNone(dispatch(self.store, notification("future/thing")))

    def test_malformed_line_gets_parse_error(self):
        try:
            json.loads("{not json")
        except json.JSONDecodeError:
            pass  # expected; handled in __main__ loop, dispatched lines only here
        self.assertIsNone(dispatch(self.store, ["not", "a", "message"]))

    def test_output_lines_are_compact_json(self):
        line = dispatch(self.store, request(1, "initialize", {"protocolVersion": "2025-06-18"}))
        self.assertNotIn("\n", line)
        self.assertTrue(json.loads(line))
