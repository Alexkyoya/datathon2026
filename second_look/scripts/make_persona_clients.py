"""Write the demo personas (robert, mateo, amira) as stored clients for this component.

Source of truth: ../data/profiles.json (holdings in CAD). Units = CAD value / close_cad in the
decision-date snapshot, so the engine values them back to the same CAD amounts.
Run after `python -m second_look demo` (which builds the snapshot):
    uv run --directory second_look python scripts/make_persona_clients.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-11-13"
NBF = {"income": "Income", "conservative": "Conservative", "balanced": "Balanced",
       "growth": "Growth", "max_growth": "Maximum growth"}

profiles = json.loads((ROOT.parent / "data" / "profiles.json").read_text(encoding="utf-8"))
quotes = json.loads((ROOT / "data" / "runtime" / "snapshots" / f"{DATE}.json").read_text())["quotes"]
out = ROOT / "examples" / "clients"
out.mkdir(parents=True, exist_ok=True)
for cid, c in profiles["clients"].items():
    tol = profiles["profiles"][c["profile"]]["assumptions"]["loss_tolerance"]
    holdings = {t: v / quotes[t]["close_cad"] for t, v in c["holdings_cad"].items() if t != "CASH"}
    client = {"client_id": cid, "name": c["name"], "age": c["age"], "profile": NBF[c["profile"]],
              "horizon_years": c["horizon_years"], "objectives": c["objectives"],
              "loss_tolerance_fraction": tol,
              "preferences": {"excluded_tickers": c.get("excluded_tickers", []), "excluded_sectors": []},
              "cash_cad": c["holdings_cad"]["CASH"], "holdings_as_of": DATE, "holdings": holdings,
              "fictional": True, "source": "data/profiles.json (team persona file)"}
    (out / f"{cid}.json").write_text(json.dumps(client, indent=2) + "\n")
    print("wrote", out / f"{cid}.json")
