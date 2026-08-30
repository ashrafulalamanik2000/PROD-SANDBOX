"""Shared HTTP client + output helpers for operator commands.

Console MVP rule (docs/CONSOLE-MVP-PLAN.md): every read command consumes
the same HTTP API as the dashboard — one state model. This module is the
single place that knows how to connect, how to fail, and how to emit
machine-readable output; commands own only their human rendering.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import typer

from .config import Config


def client(cfg: Config) -> httpx.Client:
    """Authenticated client for the dispatcher API, or exit 2 with advice."""
    if not cfg.telemetry_enabled:
        typer.secho("api.url and api.key must be configured "
                    "(key in ~/.sdtools/config.toml, url from the fleet config)",
                    fg="red")
        raise typer.Exit(2)
    return httpx.Client(base_url=cfg.api_url.rstrip("/"), timeout=15,
                        headers={"Authorization": f"Bearer {cfg.api_key}"})


def fetch(api: httpx.Client, path: str, params: dict | None = None) -> Any:
    """GET and return parsed JSON; render transport/HTTP errors and exit."""
    try:
        r = api.get(path, params={k: v for k, v in (params or {}).items()
                                  if v not in (None, "")})
    except httpx.HTTPError as exc:
        typer.secho(f"api unreachable: {exc}", fg="red")
        raise typer.Exit(1) from exc
    if r.status_code >= 400:
        typer.secho(f"{path} -> {r.status_code}: {r.text[:200]}", fg="red")
        raise typer.Exit(1)
    return r.json()


def post(api: httpx.Client, path: str, body: dict | None = None) -> Any:
    """POST and return parsed JSON; render transport/HTTP errors and exit."""
    try:
        r = api.post(path, json=body)
    except httpx.HTTPError as exc:
        typer.secho(f"api unreachable: {exc}", fg="red")
        raise typer.Exit(1) from exc
    if r.status_code >= 400:
        typer.secho(f"{path} -> {r.status_code}: {r.text[:200]}", fg="red")
        raise typer.Exit(1)
    return r.json()


def emit_json(obj: Any) -> None:
    """The one JSON emitter: stable, indented, datetimes as ISO strings."""
    typer.echo(json.dumps(obj, indent=2, default=str, sort_keys=True))
