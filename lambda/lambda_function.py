"""
Second Look - tools Lambda.

One Lambda, several actions. The agents call it with {"action": "...", ...}.
Everything here is plain code: no LLM, same input -> same output.

Actions
  get_profile     client profile + holdings + limits
  rule_check      green / yellow / red for a proposed trade (placeholder of Oliver's rule engine, works end to end)
  risk_metrics    volatility, max drawdown, recovery, 2022 replay, custom shocks (CAD)
  macro_snapshot  latest macro values as they were known on the decision date
  news_check      official items published before the decision date (from the news snapshot file)

Data location
  On AWS: env DATA_BUCKET + DATA_PREFIX (S3).
  Locally: env SECOND_LOOK_LOCAL=1 and DATA_DIR=<folder>.
"""
import io
import json
import math
import os
from functools import lru_cache

import numpy as np
import pandas as pd

LOCAL = os.getenv("SECOND_LOOK_LOCAL", "0") == "1"
DATA_DIR = os.getenv("DATA_DIR", "data")
DATA_BUCKET = os.getenv("DATA_BUCKET", "")
DATA_PREFIX = os.getenv("DATA_PREFIX", "data/")

DATA_WINDOW = "2021-11-12 to 2026-11-12"
PRICE_SOURCE = "Yahoo Finance via the AlphaForge starter dataset (adj_close), converted to CAD with Bank of Canada USD/CAD"

# Broad, diversified funds: not treated as a single issuer
BROAD_ETFS = {"XIU.TO", "SPY", "QQQ", "ESGV", "XBB.TO", "ZST.TO", "AGG", "TLT"}
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
    """Buy: paid from CASH first, then other positions are sold pro rata. Sell: proceeds go to CASH."""
    h = {k: float(v) for k, v in holdings.items()}
    amt = float(amount_cad)
    if side == "sell":
        amt = min(amt, h.get(ticker, 0.0))
        h[ticker] = h.get(ticker, 0.0) - amt
        h["CASH"] = h.get("CASH", 0.0) + amt
    else:
        total = sum(h.values())
        amt = min(amt, total)
        from_cash = min(h.get("CASH", 0.0), amt)
        h["CASH"] = h.get("CASH", 0.0) - from_cash
        rest = amt - from_cash
        others = {k: v for k, v in h.items() if k not in ("CASH", ticker) and v > 0}
        pool = sum(others.values())
        for k, v in others.items():
            h[k] = v - rest * v / pool if pool else v
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


def _limits(c, prof, buffer_):
    """Every rule as (name, low, high, value_fn, hard). Hard rules go straight to red when broken."""
    rules = []
    for cls, (lo, hi) in prof["ranges"].items():
        rules.append((f"{cls}_range", lo, hi, lambda w, cls=cls: class_weights(w)[cls], False))
    a = prof["assumptions"]
    rules.append(("single_stock_max", 0.0, a["single_stock_max"],
                  lambda w: max([x for t, x in single_issuer_weights(w).items() if t not in CRYPTO and asset_class(t) == "equity"] or [0.0]), True))
    rules.append(("crypto_max", 0.0, a["crypto_max"], lambda w: sum(x for t, x in w.items() if t in CRYPTO), True))
    excl = set(c.get("excluded_tickers", []))
    rules.append(("excluded_holdings", 0.0, 0.0, lambda w: sum(x for t, x in w.items() if t in excl), True))
    return rules


def _deviation(v, lo, hi):
    return max(0.0, lo - v, v - hi)


def rule_check(ev):
    """PLACEHOLDER of the rule engine (Oliver replaces the logic, keeps the output format)."""
    data, c, prof = get_client(ev["client_id"])
    buf = data["near_limit_buffer"]
    before = weights(c["holdings_cad"])
    after_h = simulate_trade(c["holdings_cad"], ev["ticker"], ev["amount_cad"], ev.get("side", "buy"))
    after = weights(after_h)
    order = {"green": 0, "yellow": 1, "red": 2}
    verdict, findings = "green", []

    for name, lo, hi, fn, hard in _limits(c, prof, buf):
        vb, va = fn(before), fn(after)
        db, da = _deviation(vb, lo, hi), _deviation(va, lo, hi)
        if da == 0 and db == 0:
            effect = "none"
        elif db == 0:
            effect = "creates"
        elif da > db + 1e-9:
            effect = "worsens"
        elif da < db - 1e-9:
            effect = "reduces"
        else:
            effect = "unchanged"
        level = "green"
        if effect in ("creates", "worsens"):
            level = "red" if (hard or da > buf) else "yellow"
        elif effect == "none" and not hard:
            # close to a bound and the trade moved toward it
            near_hi = hi - va < buf and va > vb + 1e-9
            near_lo = lo > 0 and va - lo < buf and va < vb - 1e-9  # a minimum of 0% is not a risk bound
            if near_hi or near_lo:
                level, effect = "yellow", "approaches_limit"
        if level != "green" or effect in ("reduces", "unchanged"):
            findings.append({"rule": name, "limit_low": _f(lo), "limit_high": _f(hi),
                             "before": _f(vb), "after": _f(va), "effect": effect, "level": level,
                             "source": "NBF model portfolio range" if name.endswith("_range") else "team assumption"})
        if order[level] > order[verdict]:
            verdict = level

    th = data["hhi_thresholds"]
    hb, ha = hhi(before), hhi(after)
    hhi_level = "green"
    if ha > hb + 1e-9:
        hhi_level = "red" if ha > th["concentrated"] else ("yellow" if ha > th["watch"] else "green")
    if hhi_level != "green":
        findings.append({"rule": "hhi_concentration", "limit_high": th["watch"], "before": _f(hb), "after": _f(ha),
                         "effect": "worsens", "level": hhi_level, "source": "team assumption"})
        if order[hhi_level] > order[verdict]:
            verdict = hhi_level

    return {
        "verdict": verdict, "client_id": ev["client_id"], "profile": c["profile"],
        "trade": {"ticker": ev["ticker"], "side": ev.get("side", "buy"), "amount_cad": ev["amount_cad"]},
        "decision_date": ev.get("decision_date", "2026-11-12"),
        "findings": findings,
        "class_weights_before": {k: _f(v) for k, v in class_weights(before).items()},
        "class_weights_after": {k: _f(v) for k, v in class_weights(after).items()},
        "weights_after": {k: _f(v) for k, v in sorted(after.items(), key=lambda kv: -kv[1])},
        "hhi_before": _f(hb), "hhi_after": _f(ha),
        "engine": "placeholder_v0 (to be replaced by the team rule engine)",
    }


def _path_stats(nav):
    dd = nav / nav.cummax() - 1
    trough = dd.idxmin()
    peak = nav[:trough].idxmax()
    after = nav[trough:]
    rec = after[after >= nav[peak]]
    rec_date = rec.index[0] if len(rec) else None
    return {
        "max_drawdown": _f(dd.min()), "peak_date": str(peak.date()), "trough_date": str(trough.date()),
        "recovery_date": str(rec_date.date()) if rec_date is not None else None,
        "months_peak_to_recovery": _f(((rec_date or nav.index[-1]) - peak).days / 30.44, 1),
        "recovered": rec_date is not None,
    }


def _portfolio_nav(w, px):
    """Buy-and-hold path for a set of weights (CASH earns 0)."""
    cols = [t for t in w if t != "CASH"]
    sub = px[cols].dropna()
    rel = sub / sub.iloc[0]
    nav = (rel * pd.Series({t: w[t] for t in cols})).sum(axis=1) + w.get("CASH", 0.0)
    return nav


def risk_metrics(ev):
    """Historical risk for tickers and/or a portfolio, in CAD. Illustrates the past, never predicts."""
    px = prices_cad()
    out = {"window": DATA_WINDOW, "currency": "CAD", "source": PRICE_SOURCE, "assets": {}}
    for t in ev.get("tickers", []):
        s = px[t].dropna()
        r = s.pct_change().dropna()
        out["assets"][t] = {"vol_annual": _f(r.std() * np.sqrt(252)),
                            "total_return": _f(s.iloc[-1] / s.iloc[0] - 1), **_path_stats(s)}

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
        nav = _portfolio_nav(w, px)
        r = nav.pct_change().dropna()
        out["portfolio"] = {"weights": {k: _f(v) for k, v in w.items()},
                            "vol_annual": _f(r.std() * np.sqrt(252)), **_path_stats(nav)}
        # Historical scenario: 2022 (rates up fast, stocks and bonds down together)
        p22 = px.loc["2022-01-03":"2022-12-30"]
        nav22 = _portfolio_nav(w, p22)
        out["scenarios"] = [{"name": "2022 replay", "type": "historical",
                             "period": "2022-01-03 to 2022-12-30",
                             "portfolio_return": _f(nav22.iloc[-1] / nav22.iloc[0] - 1),
                             "max_drawdown_in_period": _path_stats(nav22)["max_drawdown"],
                             "note": "What these weights would have done in 2022. Not a forecast."}]
        # Custom shocks: {"equity": -0.2} or {"NVDA": -0.3}; ticker shocks override class shocks
        for sc in ev.get("shocks", []):
            shock = sc["shock"]
            impact = 0.0
            for t, x in w.items():
                s_ = shock.get(t, shock.get(asset_class(t), 0.0))
                impact += x * s_
            out["scenarios"].append({"name": sc.get("name", "custom shock"), "type": "hypothetical",
                                     "shock": shock, "portfolio_return": _f(impact),
                                     "note": "Instant hypothetical shock applied to current weights. Assumption, not a forecast."})
    return out


MONTHLY = {"STATIC_TOTALCPICHANGE", "CPIAUCSL", "UNRATE"}


def macro_snapshot(ev):
    """Latest value of each macro series known on the decision date. Monthly series are lagged 1 month
    because they are dated to the reference month, not the release date."""
    d = pd.Timestamp(ev.get("decision_date", "2026-11-12"))
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
    d = ev.get("decision_date", "2026-11-12")
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
