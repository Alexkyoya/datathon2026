"""Guardrail wrapper: off when unconfigured, blocks on intervention, fails open on errors."""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
import guardrails as G  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "second_look"))
render_answer = importlib.import_module("second_look.conversation").render_answer


class FakeRuntime:
    def __init__(self, action=None, boom=False):
        self.action, self.boom, self.calls = action, boom, []

    def apply_guardrail(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise RuntimeError("service down")
        return {"action": self.action, "assessments": [
            {"topicPolicy": {"topics": [{"name": "Price predictions and guarantees", "action": "BLOCKED"}]}}]}


def setup(monkeypatch, runtime, gid="gr-1"):
    monkeypatch.setattr(G.config, "GUARDRAIL_INPUT_ID", gid)
    monkeypatch.setattr(G.config, "GUARDRAIL_OUTPUT_ID", gid)
    monkeypatch.setattr(G.config, "GUARDRAIL_INPUT_VERSION", "1")
    monkeypatch.setattr(G.config, "GUARDRAIL_OUTPUT_VERSION", "1")
    monkeypatch.setattr(G, "_client", runtime)


def test_off_when_not_configured(monkeypatch):
    rt = FakeRuntime("GUARDRAIL_INTERVENED")
    setup(monkeypatch, rt, gid="")
    assert G.check("anything", "INPUT") == {"blocked": False, "status": "not_configured"}
    assert rt.calls == []


def test_blocks_and_names_policy(monkeypatch):
    rt = FakeRuntime("GUARDRAIL_INTERVENED")
    setup(monkeypatch, rt)
    r = G.check("Bitcoin will double", "OUTPUT")
    assert r["blocked"] and r["policies"] == ["topic:Price predictions and guarantees"]
    assert rt.calls[0]["source"] == "OUTPUT" and rt.calls[0]["guardrailVersion"] == "1"


def test_passes(monkeypatch):
    setup(monkeypatch, FakeRuntime("NONE"))
    assert G.check("a normal reason", "INPUT")["blocked"] is False


def test_fails_open(monkeypatch):
    setup(monkeypatch, FakeRuntime(boom=True))
    r = G.check("text", "INPUT")
    assert r["blocked"] is False and r["status"] == "unavailable"


def test_replacement_messages_pass_chat_validation():
    import json
    for msg in [G.blocked_message(src, lang) for src in ("INPUT", "OUTPUT") for lang in ("en", "fr")]:
        text, refs = render_answer(json.dumps({"assistant_message": msg, "evidence_refs": []}), {})
        assert text == msg and refs == []
