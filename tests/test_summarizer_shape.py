"""Verifies the model call shape without spending tokens."""
import json
import os
import sys
import types
os.environ["ANTHROPIC_API_KEY"] = "test-key"
captured = {}

class _Block:
    type = "tool_use"; name = "report"
    input = {"headline": "tile-index indexed 238 of 240 tiles",
             "what_happened": "ok", "outcome": "success_with_warnings",
             "severity": "low", "confidence": "high"}
class _Usage:
    input_tokens = 1840
    output_tokens = 210
class _Resp:
    content = [_Block()]
    usage = _Usage()
class _Messages:
    def create(self, **kw):
        captured.update(kw)
        return _Resp()
class Anthropic:
    def __init__(self, *a, **k):
        self.messages = _Messages()

sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=Anthropic)

from api import summarizer

run = {"tool": "tile-index", "status": "ok", "duration_ms": 2_400_000,
       "actor_user": "anik", "metrics": {"tiles_indexed": 238},
       "counts": {"warning": 17}, "cmdline": "sdtools tile-index --input /data"}
payload, model, tin, tout = summarizer.summarize(run, "WRN no CRS\n... 900 elided ...")

assert payload["outcome"] == "success_with_warnings", payload
assert payload["key_numbers"] == [] and payload["problems"] == []   # defaults filled
assert tin == 1840 and tout == 210
assert captured["tool_choice"] == {"type": "tool", "name": "report"}
assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
assert captured["max_tokens"] == summarizer.MAX_OUTPUT_TOKENS
assert "tiles_indexed" in captured["messages"][0]["content"]
assert captured["tools"][0]["input_schema"]["required"]
json.dumps(captured["tools"])          # schema must be JSON-serialisable
print("summarizer call shape OK ->", model, tin, tout)

fb = summarizer.fallback_summary({"tool": "x", "status": "failed", "duration_ms": 1000,
                                  "counts": {"error": 3}, "metrics": {}})
assert fb["outcome"] == "failure" and fb["severity"] == "high"
print("fallback OK")
