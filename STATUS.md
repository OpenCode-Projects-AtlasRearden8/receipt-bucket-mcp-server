# STATUS — receipt-bucket MCP server

Date: 2026-10-26
Maintainer: Asdale Dynamics, LLC

## What was built

A small, local-first **MCP server over stdio** (Python 3.9+ standard library
only — zero third-party dependencies) that acts as a receipts bucket for
small-business bookkeeping.

**Package** (`receipt_bucket/`):
* `validation.py` — input cleaning & checks: control-character stripping,
  basename-only photo references, amounts coerced to exact integer cents,
  calendar-checked dates/months, strictly restricted bucket names.
* `storage.py` — `ReceiptStore`: one JSON file per bucket inside a data
  directory; path confinement (bucket paths may not escape the data dir;
  symlink escapes refused at load *and* write), atomic writes
  (temp file + `os.replace`), `chmod 0600` files, timestamped auto-backup
  before every write with rolling keep-10 pruning, corruption refusal with
  a restore hint, NaN/Infinity rejected on load and write.
* `receipts.py` — receipt build/update, shared search filters, sorting,
  month grouping, cents-exact summaries.
* `ledger_export.py` — bookkeeping-ready CSV for cashflow-simple-ledger
  import (11 fixed columns), negative-by-default amounts, optional total
  row, CSV/spreadsheet formula-injection guards on text cells.
* `tools.py` — 7 MCP tools: `add_receipt`, `search_receipts`, `tag_receipt`,
  `delete_receipt`, `monthly_summary`, `export_csv`, `bucket_info` — with
  JSON-Schema `inputSchema` declarations for MCP clients.
* `server.py` — minimal MCP stdio loop: newline-delimited JSON-RPC 2.0;
  `initialize` (protocol 2024-11-05 / 2025-03-26 / 2025-06-18),
  `notifications/initialized`, `ping`, `tools/list`, `tools/call`; tool
  errors reported via `isError` results; stdout strictly protocol, logs to
  stderr; exits cleanly on EOF.
* `__main__.py` — CLI (`python3 -m receipt_bucket`) with `--data-dir`,
  `--bucket`, `--currency`, `--keep-backups`, env overrides
  (`RECEIPTS_DATA_DIR` / `RECEIPTS_BUCKET` / `RECEIPTS_CURRENCY`), and a
  friendly error + exit code 2 on bad bucket names.

**Data**: `data/sample.json` — a fictional 15-receipt sample bucket
(Aug–Oct 2026), loaded with `--bucket sample --data-dir data`.

**Tooling**: `smoke_test.py` (offline end-to-end exercise of the real
server process) and `tests/` (106 offline unit tests: round-trips and
security cases). `README.md` includes a step-by-step smoke-test guide and
cashflow-simple-ledger import instructions.

## How to run it

```bash
# Smoke test everything (uses a temp dir; leaves the repo untouched)
python3 smoke_test.py

# Serve the shipped sample bucket
python3 -m receipt_bucket --data-dir data --bucket sample

# Run the test suite
python3 -m unittest discover -s tests -v

# Point an MCP client at it (stdio)
python3 -m receipt_bucket --data-dir /absolute/path/to/data --bucket receipts
```

## Verification results (2026-10-26)

* `python3 -m compileall receipt_bucket tests smoke_test.py` — clean.
* `python3 -m unittest discover -s tests -v` — **Ran 106 tests ... OK**
  (covers: validation round-trips; bucket-name/path-traversal rejection;
  symlink escape refusal with refused writes; NaN-on-load refusal; corrupt
  JSON refused with newest-backup hint; backup rotation keeps newest N and
  never mixes buckets; 0600 permissions; add→save→re-open round-trips;
  add/search/tag/delete flows with delete merchant-guard rails; cents-exact summary
  math ($10.10+$20.20+$30.25 = $60.55); CSV columns, delimiters, sign
  options, total row, quoting and formula-injection guards; MCP initialize
  echo/fallback, tools/list completeness, unknown-tool/-32601/-32700 error
  paths; and a full subprocess stdio session).
* `python3 smoke_test.py` — **all 8 steps PASS, exit 0** (initialize,
  tools/list, add, search, monthly summary, CSV export, bucket info,
  clean EOF exit).
* Manual pipe smoke via `printf ... | python3 -m receipt_bucket --bucket sample --data-dir data`
  — initializes and lists tools; read-only, sample untouched.
* `--bucket ../nope` — refused with a clear hint, exit 2.

## Known gaps

* Single-process design: no cross-process file locking (fine for one
  agent at a time, as MCP clients normally do; a second writer on the same
  bucket would race its last write).
* `flow` column is always `out` — income receipts are out of scope on
  purpose (they belong to the ledger side).
* Search/summary is exact-match/substring, not fuzzy or ranking.
* CSV column order is fixed; if a future cashflow-simple-ledger import
  needs different mapping, a mapping flag can be added later.
* No multi-currency FX conversion; mixed currencies are summarized
  separately, never merged.
* MCP protocol surface is tools-only (no resources/prompts/sampling), by design.
* Sample data is fictional placeholder content; no real receipts ship.

## Where it complements other servers

Receipts only — invoices, budgets, and the actual ledger live elsewhere
(such as cashflow-simple-ledger). `export_csv` is the planned hand-off
point between this bucket and that ledger.
