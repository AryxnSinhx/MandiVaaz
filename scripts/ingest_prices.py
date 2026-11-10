#!/usr/bin/env python3
"""
ingest_prices.py - load Punjab mandi prices into a local SQLite snapshot (mandi.db).

Term: SNAPSHOT
  Plain idea: we copy the prices to our own small database ONCE (or daily), so the demo
  never depends on a government website being up at the moment you record your video.

Three ways in (use whichever works for you):
  A) Official API   python ingest_prices.py --source ogd --api-key YOUR_DATA_GOV_IN_KEY
  B) A CSV file     python ingest_prices.py --source csv --csv prices.csv
                    columns (any order, case-insensitive):
                    state,district,market,commodity,variety,arrival_date,min_price,max_price,modal_price
                    arrival_date as dd/mm/yyyy or yyyy-mm-dd. Prices in Rs per quintal.
  C) See what is in the DB:  python ingest_prices.py --list

STATUS: written WITHOUT network access, so the data.gov.in field/filter names are UNVERIFIED.
The script checks what comes back and fails loudly instead of guessing. If A fails, paste me
the error plus the first record it prints and I will fix the adapter.
"""
import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # project root, so `from app...` works however you run this
sys.path.insert(0, str(ROOT))

from app.aliases import CROPS, MANDIS

# Resource found earlier: "Variety-wise Daily Market Prices Data of Commodity" (Agmarknet).
# NOTE: the catalog page "Current Daily Price of Various Commodities..." says "API does not exist".
DEFAULT_RESOURCE = "35985678-0d79-46b4-9ed6-6f13308a1d24"
OGD_URL = "https://api.data.gov.in/resource/{rid}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; MandiVaaz-student-project)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(
  state TEXT, district TEXT, market TEXT, commodity TEXT, variety TEXT,
  arrival_date TEXT,              -- ISO yyyy-mm-dd
  min_price REAL, max_price REAL, modal_price REAL,   -- Rs per quintal
  UNIQUE(market, commodity, variety, arrival_date)
);
CREATE INDEX IF NOT EXISTS idx_lookup ON prices(commodity, market, arrival_date);
"""

FIELD_ALIASES = {
    "state": ("state",), "district": ("district",), "market": ("market", "market_name"),
    "commodity": ("commodity", "commodity_name"), "variety": ("variety",),
    "arrival_date": ("arrival_date", "date"),
    "min_price": ("min_price", "min_x0020_price"), "max_price": ("max_price", "max_x0020_price"),
    "modal_price": ("modal_price", "modal_x0020_price"),
}


def clean(rec: dict) -> dict:
    low = {k.strip().lower().replace(" ", "_"): v for k, v in rec.items()}
    out = {}
    for field, names in FIELD_ALIASES.items():
        out[field] = next((low[n] for n in names if n in low and low[n] not in (None, "")), None)
    return out


def iso_date(s):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def save(db, rows):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    ok = skipped = 0
    for r in rows:
        r = clean(r)
        d = iso_date(r["arrival_date"])
        if not (r["market"] and r["commodity"] and d and num(r["modal_price"]) is not None):
            skipped += 1
            continue
        con.execute(
            "INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?,?,?,?,?)",
            (r["state"], r["district"], r["market"], r["commodity"], r["variety"] or "", d,
             num(r["min_price"]), num(r["max_price"]), num(r["modal_price"])))
        ok += 1
    con.commit()
    con.close()
    print(f"saved {ok} rows, skipped {skipped} (missing market/commodity/date/modal price)")


def get_json(url, params, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}: {r.text[:200]!r}")
            if r.status_code in (400, 401, 403, 404):
                break  # retrying will not help (bad key / blocked / wrong resource)
        except (requests.RequestException, ValueError) as e:
            print(f"  {type(e).__name__}: {e}")
        time.sleep(3 * (i + 1))
    return None


def fetch_ogd(api_key, resource, state="Punjab", page=500, max_pages=40):
    url = OGD_URL.format(rid=resource)
    for filt in ("filters[state.keyword]", "filters[state]", "filters[State]"):
        base = {"api-key": api_key, "format": "json", "limit": page, filt: state}
        first = get_json(url, {**base, "offset": 0})
        recs = (first or {}).get("records") or []
        if recs and all(str(clean(r)["state"]).lower() == state.lower() for r in recs[:20]):
            print(f"filter '{filt}' works; API says total={first.get('total')}")
            rows, offset = list(recs), page
            while offset < int(first.get("total") or 0) and offset < page * max_pages:
                nxt = get_json(url, {**base, "offset": offset})
                got = (nxt or {}).get("records") or []
                if not got:
                    break
                rows += got
                offset += page

                total = int(first.get("total") or 0)
                if len(rows) < total:
                    print(f"WARNING: fetched {len(rows)} of {total} records; raise --max-pages")
            return rows
        print(f"filter '{filt}' gave no usable Punjab records; first record seen: {recs[:1]}")
    sys.exit("OGD API gave no Punjab rows. Likely causes: bad/unactivated key, WAF block, "
             "wrong resource id, or no Punjab rows today. Use --source csv as the fallback.")


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def list_db(db):
    con = sqlite3.connect(db)
    n, newest = con.execute("SELECT COUNT(*), MAX(arrival_date) FROM prices").fetchone()
    print(f"{db}: {n} rows, newest date {newest}")
    print("\nCommodity names in DB (rows / latest date)  <- map these in aliases.py 'agmarknet' hints")
    for c, k, d in con.execute("SELECT commodity, COUNT(*), MAX(arrival_date) FROM prices GROUP BY 1 ORDER BY 2 DESC LIMIT 60"):
        print(f"  {c:35s} {k:5d}  {d}")
    print("\nMarkets in DB")
    for m, k, d in con.execute("SELECT market, COUNT(*), MAX(arrival_date) FROM prices GROUP BY 1 ORDER BY 2 DESC LIMIT 80"):
        print(f"  {m:35s} {k:5d}  {d}")
    print("\nCoverage for our crops x mandis (rows found via 'agmarknet' name hints):")
    for crop, ce in CROPS.items():
        hits = [m for m, me in MANDIS.items()
                if con.execute(
                    "SELECT 1 FROM prices WHERE lower(commodity) LIKE ? AND lower(market) LIKE ? LIMIT 1",
                    (f"%{ce['agmarknet'][0]}%", f"%{me['agmarknet'][0]}%")).fetchone()]
        print(f"  {crop:8s}: {', '.join(hits) or 'NONE'}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["ogd", "csv"])
    ap.add_argument("--api-key", default=os.environ.get("DATA_GOV_IN_KEY"))
    ap.add_argument("--resource", default=DEFAULT_RESOURCE)
    ap.add_argument("--csv")
    ap.add_argument("--db", default=str(ROOT / "data" / "mandi.db"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max-pages", type=int, default=400)
    a = ap.parse_args()
    if a.list:
        return list_db(a.db)
    if a.source == "ogd":
        if not a.api_key:
            sys.exit("Need --api-key or env DATA_GOV_IN_KEY (free key from your data.gov.in account)")
        rows = fetch_ogd(a.api_key, a.resource, max_pages=a.max_pages)
    elif a.source == "csv":
        if not a.csv:
            sys.exit("Need --csv path")
        rows = read_csv(a.csv)
    else:
        ap.error("pick --source ogd|csv or --list")
    save(a.db, rows)
    list_db(a.db)


if __name__ == "__main__":
    main()