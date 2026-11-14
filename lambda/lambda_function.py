"""
Second Look - tools Lambda.

One Lambda, several actions. The agents call it with {"action": "...", ...}.
Everything here is plain code: no LLM, same input -> same output.

Actions
  get_profile     client profile + holdings + limits
  rule_check      green / yellow / red from the team rule engine in second_look/ (Ariane), plus spectrum position
  risk_metrics    volatility, max drawdown, recovery, 2022 replay, custom shocks (CAD)
  macro_snapshot  latest macro values as they were known on the decision date
  news_check      official items published before the decision date (from the news snapshot file)

Data location
  On AWS: env DATA_BUCKET + DATA_PREFIX (S3).
  Locally: env SECOND_LOOK_LOCAL=1 and DATA_DIR=<folder>.
"""
import importlib
import importlib.util
import io
import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

LOCAL = os.getenv("SECOND_LOOK_LOCAL", "0") == "1"
DATA_DIR = os.getenv("DATA_DIR", "data")
DATA_BUCKET = os.getenv("DATA_BUCKET", "")
DATA_PREFIX = os.getenv("DATA_PREFIX", "data/")

DATA_WINDOW = "2021-11-12 to 2026-11-12"
DECISION_DATE = "2026-11-13"  # decided on the morning of Nov 13 with data through the Nov 12 close
PRICE_SOURCE = "Yahoo Finance via the AlphaForge starter dataset (adj_close), converted to CAD with Bank of Canada USD/CAD"

# Broad, diversified funds: not treated as a single issuer
BROAD_ETFS = {"XIU.TO", "SPY", "QQQ", "ESGV", "XBB.TO", "ZST.TO", "AGG", "TLT",
              "XIT.TO", "XLK", "XEF.TO"}  # last three added by scripts/add_assets.py
CRYPTO = {"BTC-USD", "ETH-USD"}


# ---------------------------------------------------------------- data loading
def _read_bytes(name):
    if LOCAL:
        with open(os.path.join(DATA_DIR, name), "rb") as f:
            return f.read()
    import boto3
    obj = boto3.client("s3").get_object(Bucket=DATA_BUCKET, Key=DATA_PREFIX + name)
    return obj["Body"].read()


@lru_cache(maxsize=None)
def load_json(name):
    return json.loads(_read_bytes(name))


@lru_cache(maxsize=None)
def load_csv(name):
    return pd.read_csv(io.BytesIO(_read_bytes(name)))


@lru_cache(maxsize=None)
def universe():
    u = load_csv("assets_universe.csv").set_index("ticker")
    return u


def asset_class(ticker):
    """Map a ticker to an NBF asset class. Our mapping, documented in the handoff."""
    if ticker in ("CASH", "ZST.TO"):
        return "cash"
    if ticker in CRYPTO:
        return "alternatives"
    u = universe()
    if ticker not in u.index:
        raise ValueError(f"Unknown ticker {ticker}")
    ac = u.loc[ticker, "asset_class"]
    if ac == "etf_bond":
        return "fixed_income"
    if ac in ("equity", "etf_equity"):
        return "equity"
    return "alternatives"  # etf_commodity (GLD)


@lru_cache(maxsize=None)
def prices_cad():
    """Daily adj_close in CAD on a business-day calendar, last value carried forward (max 5 days)."""
    p = load_csv("prices_daily.csv")
    p["date"] = pd.to_datetime(p["date"])
    px = p.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    cur = p.drop_duplicates("ticker").set_index("ticker")["currency"]
    # calendar: weekdays only (crypto weekend rows dropped, TSX/US holidays carried forward)
    px = px[px.index.dayofweek < 5].ffill(limit=5)
    fx = load_csv("fx_usdcad_daily.csv")
    fx["date"] = pd.to_datetime(fx["date"])
    usdcad = fx.set_index("date")["usdcad"].reindex(px.index).ffill(limit=5)
    for t in px.columns:
        if cur.get(t) == "USD":
            px[t] = px[t] * usdcad
    return px.dropna(how="all")


def _f(x, nd=4):
    """JSON-safe rounded float."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


# ---------------------------------------------------------------- portfolio helpers
def get_client(client_id):
    data = load_json("profiles.json")
    c = data["clients"][client_id]
    prof = data["profiles"][c["profile"]]
    return data, c, prof


def simulate_trade(holdings, ticker, amount_cad, side="buy"):
    """Same convention as the team rule engine: a buy is paid from CASH only (other holdings are never
    sold automatically); a sell adds the proceeds to CASH. Total value is unchanged."""
    h = {k: float(v) for k, v in holdings.items()}
    amt = float(amount_cad)
    if side == "sell":
        if amt > h.get(ticker, 0.0) + 1e-6:
            raise ValueError("Insufficient holdings; short sales are unsupported")
        h[ticker] = h.get(ticker, 0.0) - amt
        h["CASH"] = h.get("CASH", 0.0) + amt
    else:
        if amt > h.get("CASH", 0.0) + 1e-6:
            raise ValueError(f"Insufficient cash: {h.get('CASH', 0.0):,.0f} CAD available. "
                             "Purchases are paid from cash only (ZST.TO is not sold automatically).")
        h["CASH"] = h.get("CASH", 0.0) - amt
        h[ticker] = h.get(ticker, 0.0) + amt
    return {k: v for k, v in h.items() if v > 1e-6}


def weights(holdings):
    total = sum(holdings.values())
    return {k: v / total for k, v in holdings.items()}


def class_weights(w):
    out = {"cash": 0.0, "fixed_income": 0.0, "equity": 0.0, "alternatives": 0.0}
    for t, x in w.items():
        out[asset_class(t)] += x
    return out


def single_issuer_weights(w):
    return {t: x for t, x in w.items() if t != "CASH" and t not in BROAD_ETFS}


def hhi(w):
    """HHI on single-issuer positions only (broad ETFs counted as diversified)."""
    return sum(x * x for x in single_issuer_weights(w).values())


# ---------------------------------------------------------------- actions
def get_profile(ev):
    data, c, prof = get_client(ev["client_id"])
    w = weights(c["holdings_cad"])
    return {
        "client_id": ev["client_id"], "client": c, "profile_name": c["profile"],
        "ranges": prof["ranges"], "assumptions": prof["assumptions"],
        "current_class_weights": {k: _f(v) for k, v in class_weights(w).items()},
        "portfolio_value_cad": _f(sum(c["holdings_cad"].values()), 2),
        "sources": {"ranges": "National Bank Financial, Investment Strategy, Fall 2026 (model portfolios)",
                    "assumptions": "Second Look team assumptions", "client": "fictional persona"},
    }


# ---------------------------------------------------------------- team rule engine (second_look/)
NBF_PROFILE = {"income": "Income", "conservative": "Conservative", "balanced": "Balanced",
               "growth": "Growth", "max_growth": "Maximum growth"}
_HERE = Path(__file__).resolve().parent


def _engine_paths():
    if (_HERE / "sl_engine").is_dir():  # inside the deployed Lambda zip
        return _HERE / "sl_engine", _HERE / "sl_config"
    return _HERE.parent / "second_look" / "second_look", _HERE.parent / "second_look" / "config"


@lru_cache(maxsize=None)
def engine():
    """Load Ariane's engine under the name sl_engine (avoids clashing with agents/second_look.py)."""
    pkg, cfg = _engine_paths()
    if "sl_engine" not in sys.modules:
        spec = importlib.util.spec_from_file_location("sl_engine", pkg / "__init__.py",
                                                      submodule_search_locations=[str(pkg)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sl_engine"] = mod
        spec.loader.exec_module(mod)
    rules = importlib.import_module("sl_engine.rules")
    cfg_json = lambda n: json.loads((cfg / n).read_text(encoding="utf-8"))  # noqa: E731
    return rules, cfg_json("assets.json"), cfg_json("policy.json"), cfg_json("reference_portfolios.json")


def rule_check(ev):
    """Green / yellow / red from the team rule engine (second_look/second_look/rules.py),
    run on the stored client's CAD holdings. Output keeps the field names the agents use."""
    _, c, prof = get_client(ev["client_id"])
    rules, assets, policy, refs = engine()
    holdings = {("CASH_CAD" if k == "CASH" else k): float(v) for k, v in c["holdings_cad"].items()}
    client = {"client_id": ev["client_id"], "profile": NBF_PROFILE[c["profile"]],
              "horizon_years": c["horizon_years"], "objectives": c["objectives"],
              "loss_tolerance_fraction": prof["assumptions"]["loss_tolerance"],
              "preferences": {"excluded_tickers": c.get("excluded_tickers", [])}}
    req = {"client_id": ev["client_id"], "decision_date": ev.get("decision_date", DECISION_DATE),
           "ticker": ev["ticker"], "side": ev.get("side", "buy"), "amount_cad": float(ev["amount_cad"])}
    r = rules.evaluate_trade(req, client, holdings, assets, policy, refs)
    cl = r.get("classification") or {}

    def spot(key):
        x = cl.get(key) or {}
        return {"score": _f(x.get("score")), "distance": _f(x.get("distance")),
                "membership": {k: _f(v) for k, v in (x.get("membership_weights") or {}).items() if v > 1e-10}}

    return {
        "verdict": r["verdict"] or "manual_review",
        "client_id": ev["client_id"], "profile": client["profile"], "decision_date": req["decision_date"],
        "trade": {"ticker": req["ticker"], "side": req["side"], "amount_cad": req["amount_cad"]},
        "findings": [{"rule": x["rule"], "level": x["severity"], "bound": x["bound"], "limit": _f(x["limit"]),
                      "before": _f(x["before"]), "after": _f(x["after"]), "effect": x["effect"],
                      "source": x["source"]} for x in r["alerts"]],
        "reasons": r["reasons"],
        "unresolved": r["coverage"]["unresolved"],
        "class_weights_before": {k: _f(v) for k, v in r["weights_before"].items()},
        "class_weights_after": {k: _f(v) for k, v in r["weights_after"].items()},
        "weights_after": {k: _f(v) for k, v in sorted(r["position_weights_after"].items(), key=lambda kv: -kv[1])},
        "hhi_before": _f(r["hhi_before"]), "hhi_after": _f(r["hhi_after"]),
        "spectrum": {"target_profile": client["profile"], "target_score": _f(cl.get("target_score")),
                     "before": spot("before"), "after": spot("after"),
                     "note": "Position on the NBF model-portfolio spectrum (0 = Income ... 4 = Maximum growth)."},
        "portfolio_value_cad": _f(r["portfolio_value_cad"], 2),
        "engine": f"team rule engine, policy {r['policy_version']}, references {r['reference_portfolio_version']}",
        "assumptions": r["assumptions"],
    }


def _path_stats(nav):
    if len(nav) < 2:
        return dict.fromkeys(("max_drawdown", "peak_date", "trough_date", "recovery_date",
                              "months_peak_to_recovery", "recovered"))
    dd = nav / nav.cummax() - 1
    trough = dd.idxmin()
    peak = nav[:trough].idxmax()
    after = nav[trough:]
    rec = after[after >= nav[peak]]
    rec_date = rec.index[0] if len(rec) else None
    return {
        "max_drawdown": _f(dd.min()), "peak_date": str(peak.date()), "trough_date": str(trough.date()),
        "recovery_date": str(rec_date.date()) if rec_date is not None else None,
        "months_peak_to_recovery": _f((rec_date - peak).days / 30.44, 1) if rec_date is not None else None,
        "recovered": rec_date is not None,
    }


def _portfolio_nav(w, px):
    """Buy-and-hold path for a set of weights (CASH earns 0)."""
    cols = [t for t in w if t != "CASH"]
    sub = px[cols].dropna()
    if sub.empty:
        return pd.Series(dtype=float, index=sub.index)
    rel = sub / sub.iloc[0]
    nav = (rel * pd.Series({t: w[t] for t in cols})).sum(axis=1) + w.get("CASH", 0.0)
    return nav


def risk_metrics(ev):
    """Historical risk for tickers and/or a portfolio, in CAD. Illustrates the past, never predicts."""
    decision_date = pd.Timestamp(ev.get("decision_date", DECISION_DATE))
    px = prices_cad().loc[:decision_date]
    window = f"{px.index[0].date()} to {px.index[-1].date()}" if len(px) else None
    out = {"window": window, "currency": "CAD", "source": PRICE_SOURCE, "assets": {}}
    for t in ev.get("tickers", []):
        s = px[t].dropna()
        r = s.pct_change().dropna()
        out["assets"][t] = {
            "vol_annual": _f(r.std() * np.sqrt(252)),
            "total_return": _f(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else None,
            **_path_stats(s),
        }

    w = None
    if ev.get("weights"):
        w = {k: float(v) for k, v in ev["weights"].items()}
    elif ev.get("client_id"):
        _, c, _ = get_client(ev["client_id"])
        h = c["holdings_cad"]
        if ev.get("ticker"):
            h = simulate_trade(h, ev["ticker"], ev["amount_cad"], ev.get("side", "buy"))
        w = weights(h)
    if w:
        if any(not math.isfinite(x) or x < 0 for x in w.values()) or not math.isclose(
            sum(w.values()), 1.0, abs_tol=1e-6
        ):
            raise ValueError("Portfolio weights must be finite, nonnegative, and sum to one")
        w = {t: x for t, x in w.items() if x}
        nav = _portfolio_nav(w, px)
        r = nav.pct_change().dropna()
        out["portfolio"] = {"weights": {k: _f(v) for k, v in w.items()},
                            "vol_annual": _f(r.std() * np.sqrt(252)), **_path_stats(nav)}

        # A full calendar-year replay begins at the close immediately before 2022.
        p22 = px.loc["2021-12-31":"2022-12-30"]
        nav22 = _portfolio_nav(w, p22)
        full_replay = (
            len(nav22) >= 2
            and nav22.index[0] == pd.Timestamp("2021-12-31")
            and nav22.index[-1] == pd.Timestamp("2022-12-30")
        )
        out["scenarios"] = [{
            "name": "2022 replay", "type": "historical", "period": "2021-12-31 to 2022-12-30",
            "portfolio_return": _f(nav22.iloc[-1] / nav22.iloc[0] - 1) if full_replay else None,
            "max_drawdown_in_period": _path_stats(nav22)["max_drawdown"] if full_replay else None,
            "note": ("CAD buy-and-hold replay using the analyzed weights and prior year-end close."
                     if full_replay else "Full 2022 replay unavailable at the selected date or data coverage."),
        }]

        # First-order bond price change: -modified duration × change in yields.
        # The duration is a team assumption for the fixed-income sleeve.
        rate_shock = {"name": "Parallel +200 bp rate shock", "shock": {"fixed_income": -6.0 * 0.02}}
        valid_keys = set(px.columns) | {"CASH", "cash", "fixed_income", "equity", "alternatives"}
        for sc in [rate_shock, *(ev.get("shocks") or [])]:
            shock = sc["shock"]
            if any(k not in valid_keys or isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v < -1 for k, v in shock.items()):
                raise ValueError("Shocks must map known tickers or asset classes to finite decimal returns >= -1")
            impact = sum(x * (shock[t] if t in shock else shock.get(asset_class(t), 0.0))
                         for t, x in w.items())
            note = ("Team assumption: yields rise 200 bp and fixed-income modified duration is 6 years. "
                    "First-order bond price impact only, with cash and other sleeves held flat."
                    if sc is rate_shock else
                    "Instant hypothetical shock on the analyzed CAD weights. Ticker shocks override class shocks.")
            out["scenarios"].append({"name": sc.get("name", "custom shock"), "type": "hypothetical",
                                     "shock": shock, "portfolio_return": _f(impact), "note": note})

        out["scenarios"].append({
            "name": "NBF Fall 2026 baseline", "type": "hypothetical", "shock": {},
            "portfolio_return": None,
            "note": "Unavailable: the exact NBF forecast and a return mapping for the held assets are needed.",
        })
    return out


MONTHLY = {"STATIC_TOTALCPICHANGE", "CPIAUCSL", "UNRATE"}


def macro_snapshot(ev):
    """Latest value of each macro series known on the decision date. Monthly series are lagged 1 month
    because they are dated to the reference month, not the release date."""
    d = pd.Timestamp(ev.get("decision_date", DECISION_DATE))
    m = load_csv("macro_rates.csv").copy()
    m["date"] = pd.to_datetime(m["date"])
    m["available"] = m.apply(lambda r: r["date"] + pd.DateOffset(months=1) if r["series_id"] in MONTHLY else r["date"], axis=1)
    out = []
    for sid, g in m[m["available"] <= d].sort_values("date").groupby("series_id"):
        last = g.iloc[-1]
        yr = g[g["date"] <= last["date"] - pd.DateOffset(years=1)]
        prev = yr.iloc[-1]["value"] if len(yr) else None
        val, unit = last["value"], last["unit"]
        item = {"series_id": sid, "label": last["label"], "value": _f(val, 3), "unit": unit,
                "reference_date": str(last["date"].date()), "source": last["source"],
                "value_1y_earlier": _f(prev, 3)}
        if sid == "CPIAUCSL" and prev:
            item["yoy_inflation_pct"] = _f((val / prev - 1) * 100, 2)
        out.append(item)
    return {"decision_date": str(d.date()), "series": out,
            "note": "Monthly series shifted by one month to avoid using data before publication. FRED values are latest revisions."}


def news_check(ev):
    """Return official items for a ticker (and macro items) published on or before the decision date.
    Classification of claims (fact / prediction / opinion / unverified) is done by the News agent."""
    d = ev.get("decision_date", DECISION_DATE)
    snap = load_json("news_snapshot.json")
    wanted = {ev.get("ticker", ""), "MACRO"}
    items = [i for i in snap["items"] if i["ticker"] in wanted and i["published_date"] <= d]
    return {"decision_date": d, "items": items, "snapshot_note": snap.get("_note", ""),
            "limitations": "Official sources only, from a saved snapshot. No evidence found is not the same as false."}


ACTIONS = {"get_profile": get_profile, "rule_check": rule_check, "risk_metrics": risk_metrics,
           "macro_snapshot": macro_snapshot, "news_check": news_check}


def lambda_handler(event, context=None):
    if isinstance(event, str):
        event = json.loads(event)
    action = event.get("action")
    if action not in ACTIONS:
        return {"error": f"unknown action {action}", "available": list(ACTIONS)}
    print(json.dumps({"log": "tool_call", "event": event}))  # shows up in CloudWatch Logs
    try:
        out = ACTIONS[action](event)
        print(json.dumps({"log": "tool_result", "action": action, "verdict": out.get("verdict")}))
        return out
    except Exception as e:  # return the error to the agent instead of crashing
        print(json.dumps({"log": "tool_error", "action": action, "error": str(e)}))
        return {"error": f"{type(e).__name__}: {e}", "action": action}
