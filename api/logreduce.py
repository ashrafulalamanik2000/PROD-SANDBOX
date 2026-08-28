"""
Log reduction -- the part that makes the AI layer cheap.

A 40-minute PDAL job can emit 200k lines, 95% of which are the same line
with a different number in it. Sending that to any model is wasteful and
also makes the summary worse, because the signal drowns. So before we call
the model we:

  1. drop protocol lines (already captured as structured metrics)
  2. collapse near-duplicate lines into "line (x1842)"
  3. keep every error and warning, up to a cap, then sample evenly
  4. keep a head and tail window for context
  5. enforce a hard character budget

Typical result: 200k lines -> ~150 lines -> ~1.5k tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGITS = re.compile(r"\d+")
_PATHS = re.compile(r"(/[\w.\-]+){2,}")
_HEX = re.compile(r"0x[0-9a-fA-F]+")

DEFAULT_BUDGET_CHARS = 20_000
HEAD_LINES = 40
TAIL_LINES = 60
MAX_PROBLEM_LINES = 120


@dataclass
class Reduced:
    text: str
    lines_in: int
    lines_out: int
    est_tokens: int


def _shape(line: str) -> str:
    """Fingerprint a line so numeric/path variants collapse together."""
    s = _HEX.sub("<x>", line)
    s = _PATHS.sub("<path>", s)
    s = _DIGITS.sub("#", s)
    return s[:200]


def reduce_log(
    events: list[dict],
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> Reduced:
    """`events` is [{seq, level, stream, message}, ...] in seq order."""
    lines_in = len(events)
    body = [e for e in events if e.get("stream") != "tool"]

    problems = [e for e in body if e.get("level") in ("error", "warning")]
    if len(problems) > MAX_PROBLEM_LINES:
        step = len(problems) / MAX_PROBLEM_LINES
        problems = [problems[int(i * step)] for i in range(MAX_PROBLEM_LINES)]
    problem_seqs = {e["seq"] for e in problems}

    head = body[:HEAD_LINES]
    tail = body[-TAIL_LINES:] if len(body) > HEAD_LINES else []
    keep_seqs = {e["seq"] for e in head} | {e["seq"] for e in tail} | problem_seqs

    # Collapse the middle by shape, keeping one exemplar per shape.
    seen_shapes: dict[str, dict] = {}
    for e in body:
        if e["seq"] in keep_seqs:
            continue
        sh = _shape(e["message"])
        slot = seen_shapes.setdefault(sh, {"exemplar": e, "count": 0})
        slot["count"] += 1

    middle = [
        {**slot["exemplar"],
         "message": slot["exemplar"]["message"]
         + (f"   (x{slot['count']} similar)" if slot["count"] > 1 else "")}
        for slot in seen_shapes.values()
    ]

    selected = sorted(
        {e["seq"]: e for e in head + middle + problems + tail}.values(),
        key=lambda e: e["seq"],
    )

    # Final pass: collapse consecutive same-shape lines (the head/tail windows
    # are usually 40 copies of one progress line).
    collapsed: list[dict] = []
    for e in selected:
        sh = _shape(e["message"])
        if collapsed and collapsed[-1]["_shape"] == sh and e.get("level") == "info":
            collapsed[-1]["_run"] += 1
            collapsed[-1]["seq"] = e["seq"]
            continue
        collapsed.append({**e, "_shape": sh, "_run": 1})
    selected = [
        {**e, "message": e["message"] + (f"   (x{e['_run']} consecutive)"
                                         if e["_run"] > 1 else "")}
        for e in collapsed
    ]

    out: list[str] = []
    used = 0
    prev_seq = None
    for e in selected:
        if prev_seq is not None and e["seq"] > prev_seq + 1:
            gap = f"... {e['seq'] - prev_seq - 1} lines elided ..."
            out.append(gap)
            used += len(gap) + 1
        tag = {"error": "ERR", "warning": "WRN"}.get(e.get("level"), "   ")
        text = f"{tag} {e['message']}"[:1200]
        if used + len(text) > budget_chars:
            out.append(f"... truncated at character budget ({budget_chars}) ...")
            break
        out.append(text)
        used += len(text) + 1
        prev_seq = e["seq"]

    joined = "\n".join(out)
    return Reduced(text=joined, lines_in=lines_in, lines_out=len(out),
                   est_tokens=len(joined) // 4)
