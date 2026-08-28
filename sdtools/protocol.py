"""
The tool <-> CLI protocol.

Tools are ordinary scripts in any language. To report structured data they
print a sentinel line on stdout:

    ::sdtools:: {"type": "metric", "name": "points_out", "value": 18452331}
    ::sdtools:: {"type": "progress", "value": 0.42, "note": "tile 12/28"}
    ::sdtools:: {"type": "artifact", "path": "out/t12.laz", "kind": "laz"}
    ::sdtools:: {"type": "error", "kind": "MissingCRS", "message": "no CRS on input"}
    ::sdtools:: {"type": "field", "key": "crs", "value": "EPSG:2956"}

Anything else is captured verbatim as a log line. That means a script you
never modify still works -- it just reports less.
"""

from __future__ import annotations

import json
from typing import Any

SENTINEL = "::sdtools::"

_LEVEL_HINTS = (
    ("error", ("error", "err:", "traceback", "fatal", "exception", "failed")),
    ("warning", ("warn", "deprecat")),
)


def parse_line(line: str) -> tuple[str | None, dict[str, Any] | None]:
    """Returns (kind, payload) for sentinel lines, else (None, None)."""
    stripped = line.strip()
    if not stripped.startswith(SENTINEL):
        return None, None
    body = stripped[len(SENTINEL):].strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return payload.get("type"), payload


def infer_level(line: str, stream: str) -> str:
    """Cheap heuristic so the dashboard can filter without tool cooperation."""
    low = line.lower()
    for level, needles in _LEVEL_HINTS:
        if any(n in low for n in needles):
            return level
    return "warning" if stream == "stderr" else "info"


def emit(_type: str, **payload: Any) -> None:
    """Helper for Python tools: `from sdtools.protocol import emit`."""
    print(f"{SENTINEL} {json.dumps({'type': _type, **payload})}", flush=True)


def metric(name: str, value: float | int | str, unit: str | None = None) -> None:
    emit("metric", name=name, value=value, unit=unit)


def progress(value: float, note: str | None = None) -> None:
    emit("progress", value=round(float(value), 4), note=note)


def artifact(path: str, kind: str, **extra: Any) -> None:
    emit("artifact", path=path, kind=kind, **extra)


def error(kind: str, message: str) -> None:
    emit("error", kind=kind, message=message)


def field(key: str, value: Any) -> None:
    emit("field", key=key, value=value)
