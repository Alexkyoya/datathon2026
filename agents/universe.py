"""The universe the agents may propose, generated from assets.json at run time (never typed by hand).

assets.json is built from the organizers' provided data only, so the agents' list cannot drift from it.
No strands / AWS imports here, so tests can import it.
"""
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent


def assets_path():
    for p in (HERE.parent / "sl_config" / "assets.json",                        # API Lambda bundle
              HERE.parent / "second_look" / "config" / "assets.json"):         # repo
        if p.is_file():
            return p
    raise FileNotFoundError("assets.json not found (expected sl_config/ or second_look/config/)")


@lru_cache(maxsize=None)
def load_assets():
    return json.loads(assets_path().read_text(encoding="utf-8"))


def allowed_tickers():
    return sorted(load_assets())


def _kind(a):
    if a["asset_class"] == "cash":
        return "cash-like fund"
    if a["is_crypto"]:
        return "crypto"
    if a["is_single_stock"]:
        return f"stock, {a.get('sector') or 'sector unknown'}"
    if a["asset_class"] == "fixed_income":
        return "bond fund"
    if a["asset_class"] == "alternatives":
        return "alternative fund"
    return "stock fund"


def universe_prompt():
    """One line per allowed asset: TICKER (name; kind; facts the coordinator needs)."""
    lines = []
    for t, a in sorted(load_assets().items()):
        facts = [a["name"], _kind(a)]
        if a.get("esg") and a["esg"].get("screened"):
            facts.append("ESG-screened, excludes " + ", ".join(a["esg"]["excludes"][:3]))
        sw = a.get("sector_weights")
        if sw and not a["is_single_stock"]:
            top = max(sw, key=sw.get)
            facts.append(f"largest sector {top} {sw[top]:.0%}")
        lines.append(f"   {t} ({'; '.join(facts)})")
    return "\n".join(lines)
