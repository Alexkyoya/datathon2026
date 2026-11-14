"""Second Look on AWS: one Lambda + Function URL serving the dashboard, its API and the agents.

GET  /                       dashboard (Ariane's web/ files)
GET  /api                    usage (JSON)
GET  /api/context?client_id= stored client + references for the dashboard (default robert)
POST /api/evaluate           team rule engine (second_look/), result saved to S3
POST /api/simulations/{id}/decision
                             {"decision":"confirm"|"cancel"} or {"decision":"explain","reason":"..."}
                             explain -> the agents run here and their explanation is added to the record
POST /  (JSON, legacy)       {"client_id","ticker","amount_cad"[,"reason"]} or {"action": ...}

Every record keeps the rule engine's verdict; the agents can only explain it.
"""
import base64
import importlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "agents"))
sys.path.insert(0, str(HERE))

import config  # noqa: E402
import second_look as S  # noqa: E402  (agents/second_look.py)
import tools as T  # noqa: E402


def _pick(bundled, repo):
    """Inside the Lambda zip the component files are bundled under sl_*; locally use the repo."""
    return HERE / bundled if (HERE / bundled).exists() else HERE / repo


ENGINE = _pick("sl_engine", "second_look/second_look")
SL_CONFIG = _pick("sl_config", "second_look/config")
WEB = _pick("sl_web", "second_look/web")
CLIENTS = _pick("sl_clients", "second_look/examples/clients")
SNAPSHOTS = _pick("sl_snapshots", "second_look/data/runtime/snapshots")
DEMO_BALANCED = _pick("sl_clients/demo_balanced.json", "second_look/examples/client.json")
STATE_PREFIX = "second-look/"  # results/ and agent_requests/ under this S3 prefix
DEFAULT_CLIENT = "robert"
TOOL_ACTIONS = {"get_profile", "risk_metrics", "macro_snapshot", "news_check", "rule_check"}


def _engine():
    if "sl_engine" not in sys.modules:
        spec = importlib.util.spec_from_file_location("sl_engine", ENGINE / "__init__.py",
                                                      submodule_search_locations=[str(ENGINE)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sl_engine"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module("sl_engine.application"), importlib.import_module("sl_engine.spectrum")


class HybridStore:
    """Read-only inputs (config, clients, snapshot) come from the bundle; simulation state goes to S3."""

    def __init__(self):
        self._s3 = None

    def _state_path(self, key):
        return HERE / "runs" / "second_look_state" / key

    def _client(self):
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", region_name=config.AWS_REGION)
        return self._s3

    def load(self, key):
        if key.startswith(("results/", "agent_requests/")):
            try:
                if config.LOCAL:
                    return json.loads(self._state_path(key).read_text())
                obj = self._client().get_object(Bucket=config.DATA_BUCKET, Key=STATE_PREFIX + key)
                return json.loads(obj["Body"].read())
            except Exception:
                raise ValueError("Unknown simulation id") from None
        kind, _, name = key.partition("/")
        if kind == "config":
            path = SL_CONFIG / name
        elif kind == "clients":
            cid = name[:-5]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
                raise ValueError("Invalid client_id")
            path = DEMO_BALANCED if cid == "demo_balanced" else CLIENTS / name
        elif kind == "snapshots":
            path = SNAPSHOTS / name
        else:
            raise ValueError("Unknown store key")
        if not path.is_file():
            raise ValueError(f"Not found: {key}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, key, value):
        body = json.dumps(value, default=str)
        if config.LOCAL:
            p = self._state_path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
            return
        self._client().put_object(Bucket=config.DATA_BUCKET, Key=STATE_PREFIX + key, Body=body.encode(),
                                  ContentType="application/json")


STORE = HybridStore()

USAGE = {
    "service": "Second Look - pre-trade check for self-directed investors (PolyFinances Datathon 2026)",
    "note": "Simulation only. Not investment advice. Clients and holdings are fictional.",
    "dashboard": "Open the root URL (/) in a browser.",
    "api": {
        "GET /api/context?client_id=robert": "stored client, tickers and reference portfolios",
        "POST /api/evaluate": {"client_id": "robert", "decision_date": "2026-11-13", "ticker": "NVDA",
                               "side": "buy", "amount_cad": 24000},
        "POST /api/simulations/{id}/decision": [{"decision": "confirm"}, {"decision": "cancel"},
                                                {"decision": "explain", "reason": "..."}],
        "POST / (JSON)": {"client_id": "robert", "ticker": "NVDA", "amount_cad": 24000, "reason": "optional"},
    },
    "flow": ["green: passes without interruption", "yellow: short warning, client confirms",
             "red: asks why; the agents then explain the fixed verdict (1-2 min)"],
    "demo_clients": {"robert": "59, conservative, retiring in 3-5 years",
                     "mateo": "41, balanced, ESG preferences", "amira": "26, growth, long horizon"},
}


def _resp(code, body, content_type="application/json"):
    if content_type == "application/json":
        body = json.dumps(body, default=str)
    return {"statusCode": code, "headers": {"content-type": content_type, "cache-control": "no-store"},
            "body": body}


def _context(params):
    application, spectrum = _engine()
    cid = (params or {}).get("client_id", DEFAULT_CLIENT)
    client = STORE.load(f"clients/{cid}.json")
    assets = STORE.load("config/assets.json")
    policy = STORE.load("config/policy.json")
    refs = STORE.load("config/reference_portfolios.json")
    clients = sorted(p.stem for p in CLIENTS.glob("*.json"))
    info = {t: {k: a.get(k) for k in ("name", "asset_class", "instrument_type", "currency", "sector")}
            for t, a in assets.items()}
    return {"client": client, "clients": clients, "tickers": sorted(assets), "assets": info,
            "references": spectrum.build_references(refs, assets, policy), "reference_config": refs}


def _run_agents(record):
    """Explain a red simulation with the agents; the record's numerical verdict never changes."""
    req = record["request"]
    try:
        res = S.run(req["client_id"], req["ticker"], float(req["amount_cad"]),
                    reason=record.get("investor_reason", ""), interactive=False)
        if "error" in res:
            raise RuntimeError(res["error"])
        res.pop("rule_check", None)
        record["agent_status"] = "completed"
        record["agent_result"] = res
        record["agent_trace"] = T.TRACE
        if res.get("verdict") != record["result"]["verdict"]:
            record["agent_note"] = "Agent tools saw a different verdict; the rule engine result above is authoritative."
    except Exception as e:  # keep the simulation paused but say why
        print(f"[second-look] agent review failed: {type(e).__name__}: {e}")  # full detail -> CloudWatch
        record["agent_status"] = "failed"
        msg = str(e)
        if "AccessDenied" in msg or "not authorized" in msg:
            record["agent_error"] = "the AI model is not enabled for this AWS account"
        elif "Throttl" in msg or "Too many" in msg:
            record["agent_error"] = "the AI service is busy, please try again in a moment"
        elif "timed out" in msg.lower() or "Timeout" in msg:
            record["agent_error"] = "the AI review took too long, please try again"
        else:
            record["agent_error"] = "the AI review could not finish"
    return record


def _legacy(req):
    if req.get("action"):
        if req["action"] not in TOOL_ACTIONS:
            return _resp(400, {"error": f"unknown action {req['action']}"})
        T.reset_trace()
        a = req.pop("action")
        return _resp(200, {"result": T.call_tool(a, **req), "trace": T.TRACE})
    for k in ("client_id", "ticker", "amount_cad"):
        if k not in req:
            return _resp(400, {"error": f"missing {k}"})
    res = S.run(req["client_id"], req["ticker"], float(req["amount_cad"]), reason=req.get("reason"),
                interactive=False)
    return _resp(400 if "error" in res else 200, {"result": res, "trace": T.TRACE})


def handler(event, context=None):
    try:
        http = event.get("requestContext", {}).get("http", {})
        method = (http.get("method") or "POST").upper()
        path = event.get("rawPath") or http.get("path") or "/"
        if method == "GET":
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/index.html": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/styles.css": ("styles.css", "text/css; charset=utf-8")}
            if path in files:
                name, kind = files[path]
                return _resp(200, (WEB / name).read_text(encoding="utf-8"), kind)
            if path in ("/api", "/api/"):
                return _resp(200, USAGE)
            if path == "/api/context":
                return _resp(200, _context(event.get("queryStringParameters")))
            return _resp(404, {"error": "Not found"})

        raw = event.get("body") or ""
        if event.get("isBase64Encoded") and raw:
            raw = base64.b64decode(raw).decode()
        if not raw:
            return _resp(200, USAGE)
        if len(raw) > 16384:
            return _resp(400, {"error": "Request too large"})
        payload = json.loads(raw)
        application, _ = _engine()
        app = application.SecondLookApplication(STORE)
        if path == "/api/evaluate":
            return _resp(200, app.evaluate(payload, getattr(context, "aws_request_id", None)))
        m = re.fullmatch(r"/api/simulations/([A-Za-z0-9_-]{1,128})/decision", path)
        if m:
            if set(payload) - {"decision", "reason"} or "decision" not in payload:
                raise ValueError("Decision accepts only decision and optional reason")
            record = app.decide(m.group(1), payload["decision"], payload.get("reason"))
            if payload["decision"] == "explain":
                record = _run_agents(record)
                STORE.save(f"results/{record['simulation_id']}.json", record)
            return _resp(200, record)
        if path in ("/", "/api/check"):
            return _legacy(payload)
        return _resp(404, {"error": "Not found"})
    except (ValueError, KeyError, TypeError) as e:
        return _resp(400, {"status": "invalid_input", "verdict": None, "error": str(e)})
    except Exception as e:  # keep the dashboard informed instead of a bare 502
        return _resp(500, {"error": f"{type(e).__name__}: {e}"})
