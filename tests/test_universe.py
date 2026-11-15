"""Universe = the organizers' provided data only. Every ticker used anywhere must be in it."""
import json
import re
from pathlib import Path

import pytest

from second_look.rules import evaluate_trade
from second_look.universe import outside_universe, provided_tickers
import universe as agent_universe  # agents/universe.py

ROOT = Path(__file__).resolve().parents[1]
PROVIDED = provided_tickers()


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_provided_universe_is_the_31_starter_assets():
    assert len(PROVIDED) == 31
    assert not {"XIT.TO", "XLK", "XEF.TO"} & set(PROVIDED)


def test_assets_json_matches_provided_data():
    assets = load("second_look/config/assets.json")
    assert sorted(assets) == PROVIDED
    for t, a in assets.items():
        assert a["horizon"]["source"], t
        assert "esg" in a and "esg_tags" not in a, t


def test_profiles_and_clients_use_provided_tickers():
    profiles = load("data/profiles.json")
    for cid, c in profiles["clients"].items():
        assert outside_universe(c["holdings_cad"], PROVIDED) == [], cid
        esg = (c.get("preferences") or {}).get("esg") or {}
        assert outside_universe(esg.get("excluded_tickers", []), PROVIDED) == [], cid
    for path in (ROOT / "second_look" / "examples").rglob("*.json"):
        c = json.loads(path.read_text())
        assert outside_universe(c["holdings"], PROVIDED) == [], path.name


def test_dashboard_demos_use_provided_tickers():
    js = (ROOT / "second_look" / "web" / "app.js").read_text(encoding="utf-8")
    # The UI supplies ticker/amount scenarios; only the backend assigns a color.
    demos = re.findall(r"\['([A-Z][A-Z0-9.-]*)',\s*\d+\]", js)
    assert len(demos) >= 12
    assert outside_universe(demos, PROVIDED) == []


def test_agents_allowed_list_is_generated_from_assets_json():
    assert agent_universe.allowed_tickers() == PROVIDED
    prompt = agent_universe.universe_prompt()
    assert all(re.search(rf"^\s+{re.escape(t)} \(", prompt, re.M) for t in PROVIDED)
    source = (ROOT / "agents" / "second_look.py").read_text(encoding="utf-8")
    assert "@@UNIVERSE@@" in source and "XEF" not in source and "XLK" not in source


def test_trade_outside_universe_rejected_with_clear_message():
    assets = load("second_look/config/assets.json")
    policy = load("second_look/config/policy.json")
    client = load("second_look/examples/clients/amira.json")
    req = {"client_id": "amira", "decision_date": "2026-11-13", "ticker": "XLK", "side": "buy", "amount_cad": 100}
    with pytest.raises(ValueError, match=r"XLK is not in the asset universe \(the provided datathon data\)"):
        evaluate_trade(req, client, {"CASH_CAD": 1000.0}, assets, policy)


def test_no_price_downloads_in_code():
    allowed = {ROOT / "second_look" / "data" / "raw" / "build_dataset.py"}  # the organizers' own builder
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "build" in path.parts or path in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"^\s*(import|from)\s+(yfinance|yahoo)", text, re.M), path
