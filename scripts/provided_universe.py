"""Print the investable universe (the organizers' provided tickers) and check the project against it.

    uv run python scripts/provided_universe.py            # print + check
    uv run python scripts/provided_universe.py --fix-data # also drop non-provided rows from ./data CSVs

Source of truth: second_look/data/raw/prices_daily.csv, the committed copy of the organizer file.
No prices are downloaded. --fix-data removes rows for tickers that are not in that file (e.g. the
XIT.TO / XLK / XEF.TO rows an earlier script appended) from data/assets_universe.csv,
data/prices_daily.csv and data/corporate_actions.csv, so upload_to_s3.py ships provided data only.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "second_look"))
from second_look.universe import outside_universe, provided_tickers  # noqa: E402

DATA = ROOT / "data"


def names():
    path = DATA / "assets_universe.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["ticker"]: (r["name"], r["asset_class"]) for r in csv.DictReader(f)}


def strip(path, provided):
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    col = rows[0].index("ticker")
    keep = [rows[0]] + [r for r in rows[1:] if r[col] in provided]
    removed = len(rows) - len(keep)
    if removed:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, lineterminator="\n").writerows(keep)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-data", action="store_true")
    args = ap.parse_args()
    provided = provided_tickers()
    info = names()
    print(f"Provided universe: {len(provided)} tickers")
    for t in provided:
        name, cls = info.get(t, ("?", "?"))
        print(f"  {t:8} {cls:14} {name}")

    problems = []
    assets = json.loads((ROOT / "second_look" / "config" / "assets.json").read_text(encoding="utf-8"))
    problems += [f"assets.json: {t}" for t in outside_universe(assets, provided)]
    problems += [f"assets.json is missing provided ticker {t}" for t in sorted(set(provided) - set(assets))]
    profiles = json.loads((DATA / "profiles.json").read_text(encoding="utf-8"))
    for cid, c in profiles["clients"].items():
        problems += [f"profiles.json {cid} holds {t}" for t in outside_universe(c["holdings_cad"], provided)]
    if (DATA / "prices_daily.csv").exists():
        with open(DATA / "prices_daily.csv", newline="", encoding="utf-8") as f:
            local = {r["ticker"] for r in csv.DictReader(f)}
        extra = outside_universe(local, provided)
        if extra and args.fix_data:
            for name in ("assets_universe.csv", "prices_daily.csv", "corporate_actions.csv"):
                print(f"removed {strip(DATA / name, set(provided))} rows from data/{name}")
        elif extra:
            problems.append(f"data/prices_daily.csv has non-provided tickers {extra} (run with --fix-data)")
    if problems:
        print("\nNOT IN THE PROVIDED DATA:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("\nOK - every ticker in assets.json, profiles.json and ./data is in the provided data.")


if __name__ == "__main__":
    main()
