"""
scripts/show_rows.py -- READ-ONLY. Show the raw database rows behind a price answer.

Use it when a spoken price looks odd (e.g. potato at Rs 200 per quintal):

    uv run python -m scripts.show_rows potato ludhiana
    uv run python -m scripts.show_rows potato ludhiana --n 25

It reuses lookup.py's own query, so it shows exactly the rows lookup.py chooses from,
newest first, plus how many rows each (commodity, variety) pair has. lookup.py reports
the pair with the MOST rows, which is not necessarily the main product, so check that.
"""
import argparse
import collections
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from app.lookup import DB_PATH, _rows  # noqa: E402  (private helper, fine for a diagnostic)

ap = argparse.ArgumentParser()
ap.add_argument("crop")
ap.add_argument("mandi")
ap.add_argument("--n", type=int, default=12, help="how many newest rows to print")
args = ap.parse_args()

con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # read-only connection
try:
    rows = _rows(con, args.crop, args.mandi)
finally:
    con.close()

print(f"{args.crop} @ {args.mandi}: {len(rows)} rows in {DB_PATH}")
if not rows:
    sys.exit(0)

print("\nrows per (commodity, variety):")
for (commodity, variety), n in collections.Counter((r[0], r[1]) for r in rows).most_common():
    print(f"  {n:4d}  {commodity!r} / {variety!r}")

print(f"\nnewest {min(args.n, len(rows))} rows   (date, min, max, modal  -- Rs per quintal)")
for commodity, variety, date, lo, hi, modal in rows[: args.n]:
    flag = ""
    if None not in (lo, hi, modal) and not (lo <= modal <= hi):
        flag = "   <-- modal outside [min, max]: check this row"
    print(f"  {date}  min {lo!s:>8}  max {hi!s:>8}  modal {modal!s:>8}  {commodity} / {variety}{flag}")

prices = [r[5] for r in rows if r[5] is not None]
if prices:
    print(f"\nmodal price over all {len(prices)} rows: lowest {min(prices):.0f}, highest {max(prices):.0f}")