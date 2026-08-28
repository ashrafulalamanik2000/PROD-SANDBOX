"""
The cheap-model layer.

Contract: given a finished run's metadata plus a reduced log, return a
strict JSON object the dashboard can render without post-processing.

Cost control, in order of impact:
  1. cache by (log_digest, prompt_version) -- reruns are free
  2. reduce the log before sending (see logreduce.py)
  3. static, cacheable system prompt (prompt caching on the API side)
  4. tight max_tokens, and a forced tool call so there is no prose preamble
  5. policy: always summarize failures; summarize successes lazily
     (on first dashboard view) unless SUMMARIZE_ALL=1

Set SDTOOLS_SUMMARY_MODEL to the current cheapest Haiku model id for your
account -- check the model list in the Anthropic docs rather than trusting
the default here.
"""

from __future__ import annotations

import json
import os
from typing import Any

MODEL = os.environ.get("SDTOOLS_SUMMARY_MODEL", "claude-haiku-4-5")
PROMPT_VERSION = 3          # bump to invalidate every cached summary
MAX_OUTPUT_TOKENS = 700

SYSTEM = """You turn geospatial batch-processing logs into short status notes for a team dashboard.

Readers are colleagues who did not run the job. Some are not engineers. They want to know, in a glance: did it work, how much did it process, and is there anything they need to do.

Rules:
- Plain language. No jargon the log did not already use. Never invent numbers.
- If the log does not say something, do not guess. Set confidence to "low" instead.
- A run that exited 0 but logged many warnings is "success_with_warnings", not "success".
- "problems" are things a human should act on, phrased as what is wrong, not as log text.
- "next_steps" only when genuinely actionable. Empty list is a fine answer.
- key_numbers: pull from the provided metrics first, then the log. Format for humans
  ("18.4M points out", "2 of 240 tiles skipped").
- headline: under 90 characters, states the outcome, no trailing period.
"""

TOOL_SCHEMA: dict[str, Any] = {
    "name": "report",
    "description": "Report the structured summary of this run.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "maxLength": 90},
            "what_happened": {"type": "string"},
            "outcome": {"type": "string",
                        "enum": ["success", "success_with_warnings", "failure", "unclear"]},
            "severity": {"type": "string", "enum": ["none", "low", "medium", "high"]},
            "key_numbers": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "next_steps": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["headline", "what_happened", "outcome", "severity", "confidence"],
    },
}


def build_user_message(run: dict, reduced_log: str) -> str:
    facts = {
        "tool": run.get("tool"),
        "tool_version": run.get("tool_version"),
        "status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "duration_s": round((run.get("duration_ms") or 0) / 1000, 1),
        # Deliberately NOT sent: actor, hostname, project, run_id. Summaries are
        # cached by log content, so anything identity-specific in the prompt would
        # leak one person's name onto another person's identical run. The dashboard
        # renders who/where from its own columns.
        "command": run.get("cmdline"),
        "input_summary": run.get("input_summary"),
        "metrics": run.get("metrics"),
        "line_counts_by_level": run.get("counts"),
        "error_kind": run.get("error_kind"),
        "error_message": run.get("error_message"),
    }
    return (
        "RUN FACTS (authoritative, prefer these over the log):\n"
        f"{json.dumps(facts, indent=2, default=str)}\n\n"
        "REDUCED LOG (duplicates collapsed, gaps marked):\n"
        "<log>\n" + reduced_log + "\n</log>\n\n"
        "Call the report tool."
    )


def fallback_summary(run: dict) -> dict:
    """Used when no API key is configured or the model call fails.
    The dashboard should never show an empty cell."""
    status = run.get("status")
    warn = (run.get("counts") or {}).get("warning", 0)
    err = (run.get("counts") or {}).get("error", 0)
    ok = status == "ok"
    outcome = "success" if ok and not warn else "success_with_warnings" if ok else "failure"
    dur = round((run.get("duration_ms") or 0) / 1000, 1)
    nums = [f"{k}: {v}" for k, v in list((run.get("metrics") or {}).items())[:4]]
    return {
        "headline": f"{run.get('tool')} {status} in {dur}s",
        "what_happened": (f"{run.get('tool')} finished with status {status} after {dur}s. "
                          f"{err} error and {warn} warning lines were logged. "
                          f"(Written without the model - no ANTHROPIC_API_KEY set, "
                          f"or the model call failed.)"),
        "outcome": outcome,
        "severity": "high" if not ok else "low" if warn else "none",
        "key_numbers": nums,
        "problems": [run.get("error_message")] if run.get("error_message") else [],
        "next_steps": [],
        "confidence": "low",
    }


def summarize(run: dict, reduced_log: str) -> tuple[dict, str, int, int]:
    """Returns (summary_dict, model, tokens_in, tokens_out)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback_summary(run), "fallback", 0, 0

    from anthropic import Anthropic

    client = Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[{
            "type": "text",
            "text": SYSTEM,
            # Static across every call -> served from cache after the first.
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "report"},
        messages=[{"role": "user", "content": build_user_message(run, reduced_log)}],
    )

    payload = None
    for block in resp.content:
        if block.type == "tool_use" and block.name == "report":
            payload = block.input
            break
    if payload is None:
        return fallback_summary(run), MODEL, resp.usage.input_tokens, resp.usage.output_tokens

    payload.setdefault("key_numbers", [])
    payload.setdefault("problems", [])
    payload.setdefault("next_steps", [])
    return payload, MODEL, resp.usage.input_tokens, resp.usage.output_tokens
