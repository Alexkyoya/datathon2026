"""Write each asset's worst past fall and its recovery time into second_look/config/assets.json.

    uv run python scripts/add_asset_horizons.py                       # decision date 2026-11-13
    uv run python scripts/add_asset_horizons.py --decision-date 2027-03-01

Reads ONLY the provided data in ./data (prices_daily.csv + fx_usdcad_daily.csv, the organizers' files)
through the same code as the risk_metrics tool (lambda/lambda_function.py: prices_cad + _path_stats),
so the numbers match what the agents see. CAD prices, 5-year window ending the day before the
decision date (data through the prior close). Re-run it for a new decision date or a new ticker.

Per ticker it writes:
  "horizon": {"max_drawdown", "recovery_months", "recovered", "peak_date", "trough_date",
              "recovery_date", "window", "source"}
- recovery_months = months from the peak to the first close back at that peak (same as risk_metrics'
  months_peak_to_recovery). Not recovered by the end of the window -> null and recovered = false.
- Cash-like assets (asset_class "cash" in assets.json) are set to 0 / 0 by policy; the measured
  history is kept under "measured" for transparency.
- No price history -> values null with a "reason". Nothing is invented.
These are historical figures. They describe the past and are not forecasts.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ["SECOND_LOOK_LOCAL"] = "1"
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "lambda"))
import lambda_function as L  # noqa: E402

ASSETS = ROOT / "second_look" / "config" / "assets.json"
SOURCE = "AlphaForge provided daily prices (adj_close, in CAD with Bank of Canada USD/CAD)"
WINDOW_YEARS = 5


def horizon(series, window):
    s = series.dropna()
    if len(s) < 2:
        return {"max_drawdown": None, "recovery_months": None, "recovered": None, "window": window,
                "source": SOURCE, "reason": "no price history in the provided data for this window"}
    st = L._path_stats(s)
    return {"max_drawdown": st["max_drawdown"], "recovery_months": st["months_peak_to_recovery"],
            "recovered": st["recovered"], "peak_date": st["peak_date"], "trough_date": st["trough_date"],
            "recovery_date": st["recovery_date"], "window": window, "source": SOURCE}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision-date", default=L.DECISION_DATE)
    args = ap.parse_args()
    decision = pd.Timestamp(args.decision_date)
    last = decision - pd.Timedelta(days=1)            # data no later than the prior close
    first = last - pd.DateOffset(years=WINDOW_YEARS)  # same 5 years as risk_metrics
    px = L.prices_cad().loc[first:last]
    window = f"{px.index[0].date()}..{px.index[-1].date()}" if len(px) else None

    assets = json.loads(ASSETS.read_text(encoding="utf-8"))
    for t, a in assets.items():
        h = horizon(px[t], window) if t in px.columns else {
            "max_drawdown": None, "recovery_months": None, "recovered": None, "window": window,
            "source": SOURCE, "reason": "ticker not in the provided price file"}
        if a["asset_class"] == "cash":
            measured = {k: h.get(k) for k in ("max_drawdown", "recovery_months", "recovered")}
            h = {"max_drawdown": 0.0, "recovery_months": 0.0, "recovered": True, "window": window,
                 "source": "Second Look policy: cash-like asset (asset_class cash) counted at full value",
                 "measured": measured}
        a["horizon"] = h
        print(f"{t:8} worst fall {h['max_drawdown']!s:>8}  recovery {h['recovery_months']!s:>6} months"
              f"  {h.get('peak_date') or ''}..{h.get('recovery_date') or ('not recovered' if h.get('peak_date') else '')}")
    ASSETS.write_text(json.dumps(assets, indent=2) + "\n", encoding="utf-8")
    print(f"wrote horizon for {len(assets)} assets, window {window}")


if __name__ == "__main__":
    main()
