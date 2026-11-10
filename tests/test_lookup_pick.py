"""
Tests for the row-choosing logic in app/lookup.py (_pick, _rows, get_price).
Run:  uv run python -m tests.test_lookup_pick

Each test builds a tiny throwaway SQLite database that copies a real situation you found
in data/mandi.db with scripts/show_rows.py. Your real database is never touched.
Market and commodity names are built from the hints in app/aliases.py, so the tests follow
whatever spelling your alias table uses.
"""
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from app.aliases import CROPS, MANDIS
from app.lookup import get_price

TODAY = date(2026, 11, 5)
POTATO = CROPS["potato"]["agmarknet"][0].title()      # e.g. "Potato"
ONION = CROPS["onion"]["agmarknet"][0].title()
KHANNA = MANDIS["khanna"]["agmarknet"][0].title() + " APMC"
LUDHIANA = MANDIS["ludhiana"]["agmarknet"][0].title() + " APMC"


def make_db(rows):
    """rows: (commodity, variety, market, date, min, max, modal)"""
    folder = tempfile.mkdtemp()
    path = Path(folder) / "t.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices (commodity, variety, market, arrival_date, min_price, max_price, modal_price)")
    con.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(path)


def price(crop, mandi, db):
    return get_price(crop, mandi, None, TODAY, db)


def test_newest_row_wins_even_if_an_older_variety_label_has_more_rows():
    # The real Khanna case: 14 older rows labelled "Local", 5 newer rows labelled "Potato".
    rows = [(POTATO, "Local", KHANNA, f"2026-03-{d:02d}", 300, 300, 300) for d in (3, 5, 7, 10, 14, 16, 18)]
    rows += [(POTATO, "Local", KHANNA, f"2026-02-{d:02d}", 300, 300, 300) for d in (1, 3, 5, 8, 10, 12, 15)]
    rows += [(POTATO, "Potato", KHANNA, f"2026-03-{d:02d}", 400, 400, 400) for d in (19, 20, 23, 26, 31)]
    r = price("potato", "khanna", make_db(rows))
    assert r["found"] and r["date"] == "2026-03-31" and r["modal"] == 400


def test_sweet_potato_is_never_reported_as_potato_even_with_more_rows():
    # The real Ludhiana case had 12 potato vs 6 sweet potato; here sweet potato is made the majority.
    rows = [(POTATO, "Other", LUDHIANA, "2026-03-30", 100, 400, 200)]
    rows += [(f"Sweet {POTATO}", "Other", LUDHIANA, f"2026-03-{d:02d}", 800, 1500, 1200) for d in range(1, 12)]
    r = price("potato", "ludhiana", make_db(rows))
    assert r["modal"] == 200 and r["commodity"] == POTATO


def test_only_sweet_potato_rows_means_no_data_not_a_wrong_price():
    rows = [(f"Sweet {POTATO}", "Other", LUDHIANA, "2026-03-02", 800, 1500, 1200)]
    r = price("potato", "ludhiana", make_db(rows))
    assert r["found"] is False


def test_green_onion_is_not_reported_as_onion():
    rows = [(f"{ONION} Green", "Other", LUDHIANA, "2026-03-30", 2000, 3000, 2500)]
    assert price("onion", "ludhiana", make_db(rows))["found"] is False


def test_with_two_real_commodity_names_the_one_with_more_rows_wins():
    rows = [(POTATO, "Other", LUDHIANA, f"2026-03-{d:02d}", 100, 400, 200) for d in range(1, 6)]
    rows += [(f"{POTATO} (Red)", "Other", LUDHIANA, "2026-03-30", 500, 600, 550)]
    r = price("potato", "ludhiana", make_db(rows))
    assert r["commodity"] == POTATO and r["other_products"] == 1


def test_requested_day_picks_newest_row_on_or_before_it():
    rows = [(POTATO, "Other", LUDHIANA, d, 100, 400, m) for d, m in
            (("2026-11-01", 210), ("2026-11-04", 230), ("2026-11-05", 250))]
    r = get_price("potato", "ludhiana", "yesterday", TODAY, make_db(rows))   # yesterday = Nov 4
    assert r["date"] == "2026-11-04" and r["modal"] == 230


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")