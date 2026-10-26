"""Minimal MCP stdio server loop using only the Python standard library.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout (the standard
MCP stdio framing). Only the ``tools`` capability is implemented, which is
all this server does; logs go to stderr so stdout stays protocol-clean.

Notes on leniency, in the spirit of "beginner friendly, never condescending":
unknown notifications are ignored, unknown request methods answer with a
JSON-RPC "method not found" error, and malformed lines get a parse error.
Per the MCP spec, tool-level problems are reported as results with
``isError: true`` rather than transport errors.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import __version__
from .errors import ToolError
from .tools import HANDLERS, TOOL_DEFS

SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = "2025-06-18"

SERVER_NAME = "receipt-bucket"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _result_line(id_value, result: dict) -> str:
    return _dump({"jsonrpc": "2.0", "id": id_value, "result": result})


def _error_line(id_value, code: int, message: str, data=None) -> str:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _dump({"jsonrpc": "2.0", "id": id_value, "error": error})


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def dispatch(store, message) -> str | None:
    """Handle one incoming message dict; return a response line or None."""
    if not isinstance(message, dict):
        return None
    id_value = message.get("id")
    expect_reply = "id" in message
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    try:
        if method == "initialize":
            if not expect_reply:
                return None
            version = params.get("protocolVersion")
            if not isinstance(version, str) or version not in SUPPORTED_PROTOCOL_VERSIONS:
                version = LATEST_PROTOCOL_VERSION
            return _result_line(
                id_value,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": (
                        "Local receipts bucket for bookkeeping: add_receipt to drop a purchase, "
                        "search_receipts / tag_receipt to find and fix, monthly_summary for spend "
                        "by month and category, export_csv for a cashflow-simple-ledger import-ready "
                        "CSV, delete_receipt for corrections, bucket_info to inspect local storage. "
                        "All data stays in JSON files on this machine."
                    ),
                },
            )
        if method == "ping":
            return _result_line(id_value, {}) if expect_reply else None
        if method == "tools/list":
            return _result_line(id_value, {"tools": TOOL_DEFS}) if expect_reply else None
        if method == "tools/call":
            return _call_tool(store, params, id_value if expect_reply else None, expect_reply)
        if isinstance(method, str) and method.startswith("notifications/"):
            return None
        if expect_reply:
            return _error_line(
                id_value,
                -32601,
                f"Method not found: {method}",
                data={"supported": ["initialize", "ping", "tools/list", "tools/call"]},
            )
        return None
    except (BrokenPipeError, KeyboardInterrupt):
        raise
    except Exception:  # pragma: no cover - defensive last resort
        print("receipt-bucket server: unexpected dispatch error", file=sys.stderr)
        traceback.print_exc()
        if expect_reply:
            return _error_line(id_value, -32603, "Internal error in server dispatch")
        return None


def _call_tool(store, params, id_value, expect_reply: bool) -> str | None:
    if not expect_reply:
        return None
    name = params.get("name")
    arguments = params.get("arguments")
    if not isinstance(name, str):
        return _error_line(id_value, -32602, "tools/call requires a string 'name' parameter")
    handler = HANDLERS.get(name)
    if handler is None:
        return _error_line(
            id_value,
            -32602,
            f"Unknown tool: {name}",
            data={"available_tools": sorted(HANDLERS)},
        )
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _error_line(id_value, -32602, "tools/call 'arguments' must be an object")
    try:
        result = handler(store, arguments)
    except ToolError as exc:
        message = str(exc) if str(exc) else type(exc).__name__
        return _result_line(id_value, _text_result(f"Error: {message}", is_error=True))
    except Exception:
        print(f"receipt-bucket server: tool '{name}' crashed", file=sys.stderr)
        traceback.print_exc()
        return _result_line(
            id_value,
            _text_result("Internal error: the tool crashed. See server stderr for details.", is_error=True),
        )
    return _result_line(
        id_value,
        _text_result(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)),
    )
