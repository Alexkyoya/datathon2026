"""Amazon Bedrock Guardrails around the investor chat.

Two separate guardrails, applied with the ApplyGuardrail API (not inside the model call, so tool
results and our own JSON context are never filtered or redacted):
  - INPUT guardrail: the investor's own text. Prompt attacks, abusive content, personal identifiers.
  - OUTPUT guardrail: the text shown to the investor. Abusive content and price predictions /
    guaranteed returns (a denied topic).
Turned off when no guardrail is configured. If the guardrail service fails, the chat continues and
the failure is logged (fail-open), so a demo never stops on a guardrail outage.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

BLOCKED_INPUT = ("I can't work with that message. Please tell me in your own words why you want to make "
                 "this trade. Never share card numbers, insurance numbers, passwords or other identifiers here.")
BLOCKED_OUTPUT = ("I can't say what an investment will be worth in the future, and no return is guaranteed. "
                  "The check above is based on your profile and past data only. Tell me what you would like "
                  "to look at next.")

_client = None


def _runtime():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    return _client


def enabled(source):
    gid, _ = _ids(source)
    return bool(gid)


def _ids(source):
    if source == "INPUT":
        return config.GUARDRAIL_INPUT_ID, config.GUARDRAIL_INPUT_VERSION
    return config.GUARDRAIL_OUTPUT_ID, config.GUARDRAIL_OUTPUT_VERSION


def check(text, source):
    """Return {'blocked': bool, 'status': ..., 'policies': [...]}; never raises."""
    gid, ver = _ids(source)
    if not gid or not text or not text.strip():
        return {"blocked": False, "status": "not_configured" if not gid else "empty"}
    try:
        resp = _runtime().apply_guardrail(guardrailIdentifier=gid, guardrailVersion=ver or "DRAFT",
                                          source=source, content=[{"text": {"text": text[:20000]}}])
    except Exception as exc:  # fail-open: log and continue
        print(json.dumps({"log": "guardrail_unavailable", "source": source, "error": type(exc).__name__}))
        return {"blocked": False, "status": "unavailable", "error": type(exc).__name__}
    blocked = resp.get("action") == "GUARDRAIL_INTERVENED"
    out = {"blocked": blocked, "status": "intervened" if blocked else "passed", "policies": _policies(resp)}
    print(json.dumps({"log": "guardrail", "source": source, **out}))
    return out


def _policies(resp):
    """Which policy fired, for the audit trail (names only, never the user's text)."""
    found = []
    for a in resp.get("assessments", []):
        for t in a.get("topicPolicy", {}).get("topics", []):
            found.append("topic:" + t.get("name", ""))
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("action") == "BLOCKED":
                found.append("content:" + f.get("type", ""))
        for p in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            if p.get("action") in ("BLOCKED", "ANONYMIZED"):
                found.append("pii:" + p.get("type", ""))
    return found
