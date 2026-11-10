"""
lookup.py - find the price for (crop, mandi, day) in data/mandi.db, with honest fallbacks.

Term: PARAMETERIZED SQL
  Plain idea: the crop/mandi words go into the query as separate values (the ? marks), never
  glued into the SQL text. A weird transcript then cannot change what the query does.

Term: FALLBACK
  If today's price is missing we do NOT invent one. We report the newest older price and say
  how old it is (stale), or the same crop at the nearest mandi and say which mandi it was.

Prices are Rs per quintal (100 kg), as published by Agmarknet.

Dev check (no mic needed):  python -m app.lookup "kanak ludhiana rate"
"""
import math
import os
import sqlite3
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from app.aliases import CROPS, MANDIS
from app.parser import parse

DB_PATH = Path(os.environ.get("MANDI_DB", Path(__file__).resolve().parent.parent / "data" / "mandi.db"))
STALE_AFTER_DAYS = 3        # older than this -> reply must say the date
MAX_FALLBACK_KM = 120       # do not offer a "nearest mandi" farther than this

# Agmarknet names such as "Sweet Potato" or "Onion Green" CONTAIN our crop hint but are a different
# product. Rows whose commodity name contains one of these words are ignored for that crop.
EXCLUDE_WORDS = {"potato": ("sweet",), "onion": ("green",)}

# Approximate town-centre coordinates (my own figures, good enough to RANK neighbours, not to navigate)
COORDS = {
    "ludhiana": (30.90, 75.85), "khanna": (30.70, 76.22), "bathinda": (30.21, 74.95),
    "jalandhar": (31.33, 75.58), "amritsar": (31.63, 74.87), "patiala": (30.34, 76.39),
    "moga": (30.82, 75.17), "sangrur": (30.25, 75.84), "barnala": (30.38, 75.55),
}


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def neighbours(mandi: str):
    """Other known mandis, nearest first, within MAX_FALLBACK_KM."""
    me = COORDS[mandi]
    out = sorted(((_km(me, c), m) for m, c in COORDS.items() if m != mandi))
    return [(m, round(d)) for d, m in out if d <= MAX_FALLBACK_KM]


def _rows(con, crop, mandi):
    """All rows for this crop at this mandi, newest first. Names matched by Agmarknet name hints."""
    cw = " OR ".join("lower(commodity) LIKE ?" for _ in CROPS[crop]["agmarknet"])
    mw = " OR ".join("lower(market) LIKE ?" for _ in MANDIS[mandi]["agmarknet"])
    params = [f"%{h}%" for h in CROPS[crop]["agmarknet"]] + [f"%{h}%" for h in MANDIS[mandi]["agmarknet"]]
    sql = (f"SELECT commodity, variety, arrival_date, min_price, max_price, modal_price FROM prices "
           f"WHERE ({cw}) AND ({mw}) ORDER BY arrival_date DESC")
    rows = con.execute(sql, params).fetchall()
    bad = EXCLUDE_WORDS.get(crop, ())
    return [r for r in rows if not any(w in (r[0] or "").lower() for w in bad)]


def _pick(rows, target: date):
    """Choose the row to report.

    Rows are newest first. We group by COMMODITY NAME only (not variety): one commodity is one
    product, and its varieties ("Local", "Other", "Potato") are labels that Agmarknet changes over
    time. Grouping by variety made us ignore the newest rows whenever an older variety label had
    more rows (seen for Khanna potato: the 31 March record was skipped for an 18 March one).
    If several different commodity names remain, we report the one with the most rows.
    Returns (row, n_other_products) or (None, 0).
    """
    if not rows:
        return None, 0
    count = {}
    for r in rows:
        key = (r[0] or "").strip().lower()
        count[key] = count.get(key, 0) + 1
    main = max(count, key=count.get)
    mine = [r for r in rows if (r[0] or "").strip().lower() == main]
    on_or_before = [r for r in mine if r[2] <= target.isoformat()]
    row = (on_or_before or mine)[0]  # newest on/before the wanted day; else newest overall
    return row, len(count) - 1


def _result(crop, mandi, row, target, today, used=None, km=None, others=0):
    d = datetime.strptime(row[2], "%Y-%m-%d").date()
    return {"found": True, "crop": crop, "mandi": mandi, "mandi_used": used or mandi,
            "fallback_mandi": used is not None, "distance_km": km,
            "commodity": row[0], "variety": row[1], "date": row[2],
            "min": row[3], "max": row[4], "modal": row[5],
            "days_old": (today - d).days, "stale": (today - d).days > STALE_AFTER_DAYS,
            "wanted_day": target.isoformat(), "other_products": others}


def get_price(crop, mandi, day=None, today=None, db=None):
    """day: None/'today' = latest available, 'yesterday' = yesterday's price if it exists."""
    today = today or date.today()
    target = today - timedelta(days=1) if day == "yesterday" else today
    con = sqlite3.connect(db or DB_PATH)
    try:
        row, others = _pick(_rows(con, crop, mandi), target)
        if row:
            return _result(crop, mandi, row, target, today, others=others)
        for near, km in neighbours(mandi):
            row, others = _pick(_rows(con, crop, near), target)
            if row:
                return _result(crop, mandi, row, target, today, used=near, km=km, others=others)
        return {"found": False, "crop": crop, "mandi": mandi}
    finally:
        con.close()


def answer_text(text, today=None, db=None):
    """Text in -> parse -> lookup. The agent state machine (clarify/confirm) will wrap this later."""
    p = parse(text)
    out = {"parse": p, "prices": []}
    if p["status"] in ("ok", "confirm"):
        for m in p["mandis"]:
            out["prices"].append(get_price(p["crop"], m, p["day"], today, db))
    return out


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:] or ["kanak ludhiana rate"]:
        r = answer_text(q)
        p = r["parse"]
        print(f"\n{q!r}\n  parsed: crop={p['crop']} mandis={p['mandis']} day={p['day']} status={p['status']}")
        for pr in r["prices"]:
            if not pr["found"]:
                print(f"  NO DATA for {pr['crop']} at {pr['mandi']} or nearby")
            else:
                via = f" (via {pr['mandi_used']}, {pr['distance_km']} km away)" if pr["fallback_mandi"] else ""
                print(f"  {pr['crop']} @ {pr['mandi']}{via}: modal Rs {pr['modal']:.0f}/qtl "
                      f"(min {pr['min']:.0f}, max {pr['max']:.0f}) on {pr['date']}, "
                      f"{pr['days_old']} day(s) old{' STALE' if pr['stale'] else ''}; "
                      f"commodity={pr['commodity']!r} variety={pr['variety']!r}")