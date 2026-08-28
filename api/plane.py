"""
One-way sync into Plane (https://github.com/makeplane/plane).

Design stance: Plane is the *project* view, sdtools is the *run* view.
We push a work item per workflow so a PM sees "sundre nightly processing:
done / failed" next to everything else in the project — we do NOT mirror
every job or log line into Plane. Click-through goes the other way: the
work item's description links to the sdtools dashboard.

Verified against the published API (developers.plane.so, Aug 2026):
  auth    X-API-Key: <token>            (workspace Settings -> API tokens)
  create  POST /api/v1/workspaces/{ws}/projects/{project}/work-items/
          body {name, description_html, priority, ...} -> {id, ...}
  comment POST .../work-items/{id}/comments/   body {comment_html}
  update  PATCH .../work-items/{id}/

Older self-hosted releases exposed the same routes under `issues` instead
of `work-items`; set PLANE_RESOURCE=issues if yours does.

Config (all env; absent PLANE_API_KEY disables the whole module):

  PLANE_API_KEY        API token
  PLANE_BASE_URL       default https://api.plane.so  (self-hosted: your domain)
  PLANE_WORKSPACE      workspace slug
  PLANE_PROJECT_MAP    JSON: {"sundre-2026": "<plane project uuid>",
                              "*": "<fallback uuid>"}
  PLANE_RESOURCE       work-items (default) | issues
  DASHBOARD_URL        used for the click-through link in the description

Failures here are logged and swallowed: Plane being down must never affect
dispatch or ingest.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
from sqlalchemy import select

from .db import Job, SessionLocal, Workflow

BASE = os.environ.get("PLANE_BASE_URL", "https://api.plane.so").rstrip("/")
KEY = os.environ.get("PLANE_API_KEY")
WORKSPACE = os.environ.get("PLANE_WORKSPACE")
RESOURCE = os.environ.get("PLANE_RESOURCE", "work-items")
PROJECT_MAP: dict = json.loads(os.environ.get("PLANE_PROJECT_MAP", "{}"))
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")

ENABLED = bool(KEY and WORKSPACE and PROJECT_MAP)


def _project_id(sdtools_project: str | None) -> str | None:
    return PROJECT_MAP.get(sdtools_project or "") or PROJECT_MAP.get("*")


def _url(project_id: str, suffix: str = "") -> str:
    return (f"{BASE}/api/v1/workspaces/{WORKSPACE}/projects/{project_id}"
            f"/{RESOURCE}/{suffix}")


async def _req(method: str, url: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.request(method, url, json=body,
                                     headers={"X-API-Key": KEY})
            if r.status_code < 300:
                return r.json()
            print(f"[plane] {method} {url} -> {r.status_code} {r.text[:200]}", flush=True)
    except Exception as exc:  # noqa: BLE001 — Plane must never break dispatch
        print(f"[plane] {method} {url} failed: {exc}", flush=True)
    return None


def _wf_link(wf: Workflow) -> str:
    return (f'<p><a href="{DASHBOARD_URL}?workflow={wf.workflow_id}">'
            f"open in sdtools dashboard</a></p>" if DASHBOARD_URL else "")


async def workflow_created(workflow_id: str) -> None:
    if not ENABLED:
        return
    with SessionLocal() as db:
        wf = db.get(Workflow, workflow_id)
        if not wf:
            return
        pid = _project_id(wf.project)
        if not pid:
            return
        payload = {
            "name": f"[run] {wf.name}",
            "description_html": (
                f"<p>sdtools workflow <code>{wf.workflow_id[:8]}</code> — "
                f"{wf.total_jobs} job(s), submitted by {wf.submitted_by}.</p>"
                + _wf_link(wf)),
            "priority": "none",
            "external_source": "sdtools",
            "external_id": wf.workflow_id,
        }
    created = await _req("POST", _url(pid), payload)
    if created and created.get("id"):
        with SessionLocal() as db:
            wf = db.get(Workflow, workflow_id)
            wf.plane_issue_id = created["id"]
            db.commit()


async def workflow_finished(workflow_id: str) -> None:
    if not ENABLED:
        return
    with SessionLocal() as db:
        wf = db.get(Workflow, workflow_id)
        if not wf or not wf.plane_issue_id:
            return
        pid = _project_id(wf.project)
        if not pid:
            return
        jobs = list(db.scalars(select(Job).where(Job.workflow_id == workflow_id)))
        ok = sum(1 for j in jobs if j.state == "ok")
        failed = [j for j in jobs if j.state in ("failed", "cancelled")]
        dur = ((wf.finished_at - wf.created_at).total_seconds()
               if wf.finished_at else 0)
        lines = [f"<p><b>{wf.status.upper()}</b> — {ok}/{len(jobs)} jobs ok "
                 f"in {dur/60:.1f} min.</p>"]
        for j in failed[:10]:
            lines.append(f"<p>✕ <code>{j.step_key or j.tool}</code>: "
                         f"{j.error_kind}: {(j.error_message or '')[:200]}</p>")
        issue_id, status, name = wf.plane_issue_id, wf.status, wf.name

    await asyncio.gather(
        _req("POST", _url(pid, f"{issue_id}/comments/"),
             {"comment_html": "".join(lines)}),
        _req("PATCH", _url(pid, f"{issue_id}/"),
             {"name": f"[run {'✓' if status == 'ok' else '✕'} {status}] {name}",
              "priority": "high" if status == "failed" else "none"}),
    )
