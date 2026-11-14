"""The investable universe = the tickers in the organizers' AlphaForge / Datathon 2026 price file.

The committed copy data/raw/prices_daily.csv is the organizer file as provided (31 assets,
2021-11-12 to 2026-11-12). Nothing outside it may enter config/assets.json, client files,
prompts or demos. This module does file I/O; rules.py stays pure and only sees assets.json.
"""
import csv
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1]
PROVIDED_PRICES = COMPONENT / 'data' / 'raw' / 'prices_daily.csv'
PROVIDED_SOURCE = 'AlphaForge / Datathon 2026 starter dataset (organizer prices_daily.csv)'


def provided_tickers(prices_csv=PROVIDED_PRICES):
    """Sorted list of tickers that have prices in the provided file."""
    with open(prices_csv, newline='', encoding='utf-8') as f:
        return sorted({row['ticker'] for row in csv.DictReader(f)})


def outside_universe(tickers, provided):
    """Tickers (other than the cash ledger) that are not in the provided data."""
    allowed = set(provided) | {'CASH', 'CASH_CAD'}
    return sorted(set(tickers) - allowed)
