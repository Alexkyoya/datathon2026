"""Add extra assets to the starter dataset, using the same method as the organizers' build_dataset.py
(Yahoo Finance via yfinance, adj_close, same window 2021-11-12 to 2026-11-12).

Only the extra tickers are downloaded; the original 31 assets are left untouched.
Safe to re-run: rows for the extra tickers are replaced, not duplicated.

Run from the repo root (yfinance is only needed for this script, so it isn't added to the project):
    uv run --with yfinance python scripts/add_assets.py
    uv run python scripts/upload_to_s3.py
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA = Path(__file__).resolve().parents[1] / "data"
START, END = "2021-11-12", "2026-11-12"

# ticker, name, asset_class, market, currency, sector  (same columns as assets_universe.csv)
EXTRA = [
    ("XIT.TO", "iShares S&P/TSX Capped Information Technology Index ETF", "etf_equity", "TSX", "CAD",
     "Sector ETF (Canada, information technology)"),
    ("XLK", "Technology Select Sector SPDR Fund", "etf_equity", "NYSE Arca", "USD",
     "Sector ETF (U.S., information technology)"),
    ("XEF.TO", "iShares Core MSCI EAFE IMI Index ETF", "etf_equity", "TSX", "CAD",
     "Broad market (international developed, ex North America)"),
]


def download(tickers, ccy):
    end_excl = (date.fromisoformat(END) + timedelta(days=1)).isoformat()
    raw = yf.download(tickers, start=START, end=end_excl, auto_adjust=False, actions=True,
                      group_by="ticker", progress=False, threads=True)
    prices, actions = [], []
    for t in tickers:
        d = raw[t].dropna(subset=["Close"]).reset_index()
        if d.empty:
            raise SystemExit(f"No data returned for {t}. Check the ticker or your connection.")
        d["Date"] = pd.to_datetime(d["Date"]).dt.date
        prices.append(pd.DataFrame({
            "date": d["Date"], "ticker": t, "open": d["Open"], "high": d["High"], "low": d["Low"],
            "close": d["Close"], "adj_close": d["Adj Close"], "volume": d["Volume"].astype("int64"),
            "currency": ccy[t]}))
        for col, kind in (("Dividends", "dividend"), ("Stock Splits", "split")):
            if col in d:
                for _, r in d.loc[d[col] > 0, ["Date", col]].iterrows():
                    actions.append({"ex_date": r["Date"], "ticker": t, "action": kind, "value": r[col],
                                    "unit": f"{ccy[t]} per share" if kind == "dividend" else "split ratio (new:old)"})
    p = pd.concat(prices, ignore_index=True)
    for c in ("open", "high", "low", "close", "adj_close"):
        p[c] = p[c].round(6)
    a = pd.DataFrame(actions, columns=["ex_date", "ticker", "action", "value", "unit"])
    return p, a


def main():
    extra = pd.DataFrame(EXTRA, columns=["ticker", "name", "asset_class", "market", "currency", "sector"])
    tickers = extra["ticker"].tolist()

    uni = pd.read_csv(DATA / "assets_universe.csv")
    uni = uni[~uni["ticker"].isin(tickers)]
    start_id = len(uni) + 1
    extra.insert(0, "asset_id", [f"A{i:02d}" for i in range(start_id, start_id + len(extra))])

    new_p, new_a = download(tickers, dict(zip(extra["ticker"], extra["currency"])))
    assert (new_p["adj_close"] > 0).all(), "bad adj_close"

    prices = pd.read_csv(DATA / "prices_daily.csv")
    prices = pd.concat([prices[~prices["ticker"].isin(tickers)], new_p.astype({"date": str})], ignore_index=True)
    assert not prices.duplicated(["date", "ticker"]).any(), "duplicate (date, ticker) rows"

    acts = pd.read_csv(DATA / "corporate_actions.csv")
    acts = pd.concat([acts[~acts["ticker"].isin(tickers)], new_a.astype({"ex_date": str})], ignore_index=True)

    pd.concat([uni, extra], ignore_index=True).to_csv(DATA / "assets_universe.csv", index=False)
    prices.sort_values(["ticker", "date"]).to_csv(DATA / "prices_daily.csv", index=False)
    acts.sort_values(["ticker", "ex_date"]).to_csv(DATA / "corporate_actions.csv", index=False)

    for t, g in new_p.groupby("ticker"):
        print(f"{t:8} {len(g):5} rows  {g['date'].min()} .. {g['date'].max()}")
    print(f"OK - universe now has {len(uni) + len(extra)} assets. Next: uv run python scripts/upload_to_s3.py")


if __name__ == "__main__":
    main()
