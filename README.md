# receipt-bucket — an MCP receipts server, friendly and local-first

A small **MCP server** (Python standard library only, runs over stdio) that
works like a bucket for receipts: you or your agent drop in a purchase —
amount, merchant, category, optional tags, a note, or just a photo
filename — and later *fish back out* every receipt by search, get
**monthly spend summaries**, or produce a **bookkeeping-ready CSV** to
import into a cashflow-simple-ledger.

Everything is stored in plain JSON files on your own machine, with an
automatic backup taken before every write. No accounts, no network, no
telemetry, no secrets.

Built and maintained by **Asdale Dynamics, LLC** — the same folks behind
*The Massive AI Manual* (dylanasdale.github.io), where everything is
explained like a friend would: plain English, step by step, never
condescending.

---

## Why this server exists (and what it *doesn't* do)

This server does one small job well: **receipts**. It deliberately does not
track invoices, bank feeds, budgets, or full ledgers — your
cashflow-simple-ledger (or any other bookkeeping MCP server) keeps that job.
Instead, receipt-bucket hands it a clean CSV whenever it's time to move
receipts into the ledger. One bucket, one job, zero overlap.

## Quick start

Requirements: **Python 3.9+** (tested on 3.13). Nothing to install — stdlib only.

### 1. Smoke test

```bash
python3 smoke_test.py
```

You should see eight `[PASS]` lines. This starts the real server in a
temporary folder, talks MCP to it, and leaves your repo untouched.

### 2. Try it by hand with a pipe

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"human","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 -m receipt_bucket --bucket sample --data-dir data
```

The last reply includes every tool the server offers. The logs
("serving / 'bye'") go to **stderr**, so stdout stays protocol-clean.

### 3. Load the sample bucket

A sample bucket ships in `data/sample.json` — fifteen fictional receipts
across three months from a small author/small-business life of the LLC
(printing proofs, coffee, transit, a game-night subscription, LLC filing
fees). Point the server at it:

```bash
python3 -m receipt_bucket --data-dir data --bucket sample
```

(Sample data is entirely fictional; real receipts live in your own bucket
file, which you create just by using it.)

### 4. Add it to your MCP client

Most MCP clients use a config with an `mcpServers` block, e.g.:

```json
{
  "mcpServers": {
    "receipts-bucket": {
      "command": "python3",
      "args": [
        "-m", "receipt_bucket",
        "--data-dir", "/absolute/path/to/receipt-bucket-mcp-server/data",
        "--bucket", "receipts"
      ],
      "cwd": "/absolute/path/to/receipt-bucket-mcp-server"
    }
  }
}
```

Use an **absolute** `--data-dir` so the bucket is found no matter where the
client launches from. The server speaks plain MCP over stdio (JSON-RPC with
newline framing), supports protocol versions 2024-11-05 / 2025-03-26 /
2025-06-18, and exposes only the `tools` capability.

## The tools

| Tool | What it does |
|---|---|
| `add_receipt` | Drop a receipt in: `amount` (positive, money out), `merchant`, optional `category` (lowercase kebab-case, e.g. `office_supplies`), `date` (defaults to today), `note`, `payment_method`, `receipt_photo` (a plain filename like `IMG_0042.jpg`), `tags`, `currency`. |
| `search_receipts` | Filter by merchant substring, category, `tags` (all) or `tags_any`, date range, amount range, free text, or payment method. Returns matches plus the cents-exact total of all matches. |
| `tag_receipt` | Fix up an existing receipt: `add_tags`, `remove_tags`, `set_category`. |
| `delete_receipt` | Remove a wrong/duplicate receipt (auto-backed-up first; optional `expected_merchant` safety check). |
| `monthly_summary` | Spend grouped by calendar month with per-category and per-payment-method breakdowns; scope by `month` (`2026-09`), `year`, or any search filter. |
| `export_csv` | Bookkeeping-ready CSV for cashflow-simple-ledger import (see below). |
| `bucket_info` | Where the bucket lives, its size, counts, date range, totals, and backup status. |

All tool results come back as pretty-printed JSON in the tool's text
content, so both agents and humans can read them.

## Importing into cashflow-simple-ledger

Call `export_csv`, then copy the `csv` value in the result into a file such
as `receipts-2026-09.csv`. The columns are fixed and documented:

```csv
date,description,amount,currency,flow,category,payment_method,receipt,tags,notes,id
2026-09-05,Pixel Parlor Games,-19.99,USD,out,gaming,credit_card,,ffxiv-adjacent; monthly,,
```

Notes on the details:

* `amount` is the money-out total. By default it is **negative** (a
  cashflow ledger reads negative as spend); pass `amount_sign: "positive"`
  to flip it.
* `flow` is always `out`, since this bucket only records money-out receipts.
* `tags` are joined with `;` inside a single cell.
* `include_total_row: true` appends a final `TOTAL` row for humans — leave
  it off when importing so the ledger doesn't count it twice.
* Every *text* cell that a spreadsheet could misread as a formula (starting
  with `=`, `+`, `-`, `@` or TAB) is written with a leading apostrophe, so
  previewing the file in Excel or Google Sheets is safe. The `amount` column
  is never guarded — it must stay a plain number for the import.
* `delimiter` may be `,` (default), `;`, or `tab` for locale-friendly importers.

Import order: date, description, amount, currency, flow, category,
payment method, receipt, tags, notes, id — map them in cashflow-simple-ledger's
CSV importer accordingly.

## Storage, backups, and path safety

* **Location**: one JSON file per bucket: `<data-dir>/<bucket>.json`, next to
  a `backups/` folder. Default bucket is `receipts`; the shipped sample is
  `--bucket sample`.
* **Auto-backup**: before **every** write, the current file is copied to
  `backups/<bucket>@@YYYYmmdd-HHMMSS-<us>-<id>.json`. The newest 10 are kept
  (`--keep-backups N` to change). To restore: copy a backup over your bucket
  file.
* **Path safety**: bucket names are strictly validated (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`),
  the bucket file must resolve to exactly `<data-dir>/<bucket>.json`, and
  symlinks pointing outside the data directory are refused on load *and*
  write. Photo "filenames" are labels only — they are reduced to a bare
  basename and are never opened by this server.
* **Atomic writes**: saves go through a temp file + `os.replace`, and files
  are written with `chmod 0600` (owner-only) since receipts are private-ish.
* **Corrupted file?** The server refuses to guess: it exits with a clear
  message pointing at the newest backup you could restore instead.
* **No NaN/Infinity**: these are rejected on load and on write. Money is
  tallied in integer cents, so summaries are exact.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite is fully offline and covers validation, path-safety and traversal
attacks (including symlink escapes), NaN/corruption handling, backup
rotation, add/search/tag/summary/export/delete round-trips, CSV
formula-injection guards, the MCP protocol layer, and a full subprocess
session speaking JSON-RPC over pipes (`tests/test_integration_stdio.py`).

## How it fits your toolkit

Alongside a cashflow-simple-ledger MCP server, a document organiser, or
editorial prompts server, this one holds the **receipts** lane only. It:
* never reads photos (filenames are labels), never calls the network,
* makes receipts easy to search ("that coffee run in September"),
* makes month-end bookkeeping a copy-paste.

## License & credits

MIT — see `LICENSE`. ©  **Copyright (c) 2026 Asdale Dynamics, LLC**.
Made with care by Asdale Dynamics, LLC for *The Massive AI Manual*
(dylanasdale.github.io). No personal data or secrets are required; the
sample bucket is 100% fictional.
