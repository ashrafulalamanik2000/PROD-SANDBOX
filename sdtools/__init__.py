"""sdtools -- internal processing toolkit with run telemetry.

IMPORTANT: keep this module import-free. Tools running inside resolved
environments import `sdtools.protocol`, and those environments deliberately
do NOT contain the CLI's dependencies (pydantic, typer, httpx). Anything
imported here would become a hidden requirement of every tool environment.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
