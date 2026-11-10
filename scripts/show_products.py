"""
scripts/show_products.py -- READ-ONLY. For potato, onion and tomato: which commodity names
in data/mandi.db does lookup.py's hint match, and how many rows / which dates each has?

    uv run python scripts/show_products.py

Look for names that are a DIFFERENT product (e.g. "Sweet Potato", "Onion Green", "Tomato
Puree"). Those need a word in EXCLUDE_WORDS in app/lookup.py. Names already excluded are
marked.
"""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from app.aliases import CROPS  # noqa: E402
from app.lookup import DB_PATH  # noqa: E402

try:
    from app.lookup import EXCLUDE_WORDS  # present after scripts/apply_lookup_fix.py
except ImportError:
    EXCLUDE_WORDS = {}

con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
for crop in ("potato", "onion", "tomato"):
    hints = CROPS[crop]["agmarknet"]
    where = " OR ".join("lower(commodity) LIKE ?" for _ in hints)
    rows = con.execute(
        f"SELECT commodity, COUNT(*), MIN(arrival_date), MAX(arrival_date), COUNT(DISTINCT market) "
        f"FROM prices WHERE {where} GROUP BY commodity ORDER BY COUNT(*) DESC",
        [f"%{h}%" for h in hints],
    ).fetchall()
    print(f"\n{crop}  (hints: {hints})")
    for name, n, first, last, markets in rows:
        bad = EXCLUDE_WORDS.get(crop, ())
        tag = "   [excluded]" if any(w in name.lower() for w in bad) else ""
        print(f"  {n:6d} rows  {markets:3d} markets  {first} .. {last}  {name!r}{tag}")
con.close()
