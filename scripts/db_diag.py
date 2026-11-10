#!/usr/bin/env python3
"""
db_diag.py - read-only look inside data/mandi.db, WITHOUT using the aliases.py hints.

Why: ingest_prices.py --list (a) shows only the top 60 commodities / top 80 markets, and
(b) checks coverage using only the FIRST 'agmarknet' hint of each crop/mandi. So a 'NONE'
there can mean "hint did not match", not "no data". This script searches by keyword instead.

Run from the project root:   uv run python scripts/db_diag.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "mandi.db")

# keywords are matched as lower-case substrings of the DB's commodity / market names
CROPS = {
    "wheat": ["wheat", "gehun", "kanak"],
    "paddy": ["paddy", "dhan", "rice"],
    "cotton": ["cotton", "kapas"],
    "maize": ["maize", "corn"],
    "potato": ["potato"],
    "mustard": ["mustard", "sarson", "colza", "rapeseed", "toria"],
    "moong": ["moong", "green gram"],
    "onion": ["onion"],
    "tomato": ["tomato"],
}
MANDIS = ["ludhiana", "khanna", "bathinda", "jalandhar", "amritsar", "patiala", "moga", "sangrur", "barnala"]

con = sqlite3.connect(DB)
q = con.execute


def like_any(col, words):
    return "(" + " OR ".join(f"lower({col}) LIKE '%{w}%'" for w in words) + ")"


print("== 1. Rows per month (is the data thin, truncated, or just old?) ==")
for ym, n in q("SELECT substr(arrival_date,1,7), COUNT(*) FROM prices GROUP BY 1 ORDER BY 1"):
    print(f"  {ym}  {n:6d}")

print("\n== 2. Per crop: commodity names matched, anywhere in Punjab ==")
for crop, words in CROPS.items():
    rows = q(f"SELECT commodity, variety, COUNT(*), MAX(arrival_date) FROM prices "
             f"WHERE {like_any('commodity', words)} GROUP BY 1,2 ORDER BY 3 DESC").fetchall()
    print(f"\n  [{crop}]  " + ("NO matching commodity name" if not rows else ""))
    for c, v, n, d in rows[:8]:
        print(f"    {c:36s} variety={v or '-':18s} rows={n:4d} latest={d}")

print("\n== 3. Per crop x our 9 mandis: rows / latest date (blank = nothing) ==")
print("  " + "crop".ljust(9) + "".join(m[:8].ljust(14) for m in MANDIS))
for crop, words in CROPS.items():
    cells = []
    for m in MANDIS:
        r = q(f"SELECT COUNT(*), MAX(arrival_date) FROM prices WHERE {like_any('commodity', words)} "
              f"AND lower(market) LIKE '%{m}%'").fetchone()
        cells.append((f"{r[0]}/{r[1][5:]}" if r[0] else "-").ljust(14))
    print("  " + crop.ljust(9) + "".join(cells))

print("\n== 4. Exact market names matching our mandis (spelling check) ==")
for m in MANDIS:
    r = q("SELECT market, COUNT(*), MAX(arrival_date) FROM prices WHERE lower(market) LIKE ? GROUP BY 1",
          (f"%{m}%",)).fetchall()
    print(f"  {m:10s}", r if r else "NOT IN DB")

print("\n== 5. Commodity names ranked 61+ (the ones --list cut off) ==")
rest = q("SELECT commodity, COUNT(*), MAX(arrival_date) FROM prices GROUP BY 1 "
         "ORDER BY 2 DESC LIMIT -1 OFFSET 60").fetchall()
for c, n, d in rest:
    print(f"  {c:36s} {n:5d}  {d}")
print(f"  ({len(rest)} names)")
con.close()