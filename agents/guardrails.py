"""Amazon Bedrock Guardrails around the investor chat.

Two separate guardrails, applied with the ApplyGuardrail API (not inside the model call, so tool
results and our own JSON context are never filtered or redacted):
  - INPUT guardrail: the investor's own text. Prompt attacks, abusive content, personal identifiers.
  - OUTPUT guardrail: the text shown to the investor. Abusive or harmful content.
    (A "price predictions" denied topic was dropped: it also blocked correct risk warnings.)
Turned off when no guardrail is configured. If the guardrail service fails, the chat continues and
the failure is logged (fail-open), so a demo never stops on a guardrail outage.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

BLOCKED_INPUT = ("I can't act on that message. I can't change the check result, place a trade or handle personal "
                 "identifiers such as card numbers. You can revise the trade, cancel it, or acknowledge the risks "
                 "and proceed with the buttons on screen, or tell me in your own words what you have in mind.")
BLOCKED_OUTPUT = ("I can't say what an investment will be worth in the future, and no return is guaranteed. "
                  "The check above is based on your profile and past data only. Tell me what you would like "
                  "to look at next.")
MESSAGES = {
    "en": {"INPUT": BLOCKED_INPUT, "OUTPUT": BLOCKED_OUTPUT},
    "fr": {"INPUT": ("Je ne peux pas donner suite à ce message. Je ne peux pas modifier le résultat de la "
                     "vérification, passer une transaction ni traiter des identifiants personnels comme un numéro "
                     "de carte. Vous pouvez réviser la transaction, l'annuler, ou reconnaître les risques et "
                     "continuer avec les boutons à l'écran, ou me dire dans vos mots ce que vous avez en tête."),
           "OUTPUT": ("Je ne peux pas dire ce que vaudra un placement dans le futur, et aucun rendement n'est "
                      "garanti. La vérification ci-dessus repose seulement sur votre profil et sur des données "
                      "passées. Dites-moi ce que vous aimeriez examiner ensuite.")},
}


def blocked_message(source, language="en"):
    return MESSAGES.get(language, MESSAGES["en"])[source]


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
