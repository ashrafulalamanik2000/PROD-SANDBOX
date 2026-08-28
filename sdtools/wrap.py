"""
`sdtools wrap <folder>` — turn an existing script or agent-skill folder into
a console tool, deterministically.

Where this comes from: teams accumulate working scripts inside agent skill
folders (SKILL.md + scripts + requirements). The *scripts* are stable,
deterministic assets; the *instruction files* are the agentic layer. Wrapping
migrates the first and deliberately drops the second — the console runs code,
not prompts.

What it does (all inspectable, nothing hidden):
  1. copies the folder into tools/<name>/src/  (or references it in place
     with --in-place for folders that must stay on a network share)
  2. finds the entry script (--entry, else run.py/main.py/process.py/cli.py,
     else the only .py file)
  3. reads argparse add_argument(...) calls out of the entry script and
     drafts `params:` for tool.yaml — a best-effort DRAFT, marked as such
  4. finds requirements(.txt/.in) or pyproject dependencies and drafts
     envs/<name>/env.yaml
  5. writes tool.yaml with TODO markers on every guess

It never executes the wrapped code.
"""

from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config
from .environments import envs_root

ENTRY_CANDIDATES = ("run.py", "main.py", "process.py", "cli.py", "app.py")
AGENTIC_FILES = ("SKILL.md", "skill.md", "AGENT.md", "CLAUDE.md", "prompt.md",
                 "instructions.md", "system_prompt.txt")
_TYPE_MAP = {"int": "int", "float": "float", "str": "str", "Path": "path"}


class WrapError(Exception):
    pass


@dataclass
class DraftParam:
    name: str
    type: str = "str"
    required: bool = False
    default: object = None
    help: str = ""
    multiple: bool = False
    positional: bool = False      # child takes it with no flag, in order
    flag: str | None = None       # set when the console name != the child flag


@dataclass
class WrapReport:
    tool_dir: Path
    manifest: Path
    entry: str
    params: list[DraftParam]
    env_file: Path | None
    deps: list[str]
    agentic_ignored: list[str]
    notes: list[str]


# ------------------------------------------------------------ introspection

def _entry_traits(p: Path) -> dict:
    """Cheap static hints for ranking candidate entry scripts."""
    try:
        text = p.read_text(errors="replace")
    except OSError:
        text = ""
    return {
        "main_guard": '__name__ == "__main__"' in text or "__name__ == '__main__'" in text,
        "argparse": "add_argument" in text,
        "def_main": "def main(" in text,
        "lines": text.count("\n") + 1,
    }


def find_entry(src: Path, explicit: str | None) -> Path:
    if explicit:
        p = src / explicit
        if not p.exists():
            raise WrapError(f"--entry {explicit!r} not found in {src}")
        return p
    for name in ENTRY_CANDIDATES:
        for p in (src / name, src / "scripts" / name):
            if p.exists():
                return p
    pys = sorted(p for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    if len(pys) == 1:
        return pys[0]
    if not pys:
        raise WrapError(f"no .py files found in {src}")

    # Rank: a script with a __main__ guard AND argparse is almost certainly
    # the entry. If exactly one qualifies, take it; otherwise show the menu.
    traits = {p: _entry_traits(p) for p in pys}
    strong = [p for p, t in traits.items() if t["main_guard"] and t["argparse"]]
    if len(strong) == 1:
        return strong[0]

    lines = []
    for p in pys:
        t = traits[p]
        marks = "".join([
            " [__main__]" if t["main_guard"] else "",
            " [argparse]" if t["argparse"] else "",
            " [def main]" if t["def_main"] else "",
        ])
        star = "  <-- likely" if p in strong else ""
        lines.append(f"    --entry {p.relative_to(src).as_posix()}"
                     f"  ({t['lines']} lines){marks}{star}")
    raise WrapError(
        f"cannot pick an entry script in {src} — {len(pys)} candidates. "
        f"Rerun with one of:\n" + "\n".join(lines))


def draft_params(entry: Path) -> tuple[list[DraftParam], list[str]]:
    """Static AST scan for argparse add_argument calls. Never executes code."""
    notes: list[str] = []
    try:
        tree = ast.parse(entry.read_text(errors="replace"))
    except SyntaxError as exc:
        return [], [f"could not parse {entry.name} ({exc}); declare params by hand"]

    params: list[DraftParam] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not flags:
            continue
        long = next((f for f in flags if f.startswith("--")), None)
        kw = {k.arg: k.value for k in node.keywords if k.arg}

        if long is None:
            # Positional: passed to the child with no flag, in declared order.
            name = flags[0].replace("-", "_")
            p = DraftParam(name=name, positional=True, required=True)
            nargs = kw.get("nargs")
            if isinstance(nargs, ast.Constant):
                if nargs.value in ("?", "*"):
                    p.required = False
                if nargs.value in ("*", "+"):
                    p.multiple = True
                if isinstance(nargs.value, int) and nargs.value > 1:
                    p.multiple = True
        else:
            name = long.lstrip("-").replace("-", "_")
            p = DraftParam(name=name)
        t = kw.get("type")
        if isinstance(t, ast.Name):
            p.type = _TYPE_MAP.get(t.id, "str")
        if (any(w in name for w in ("path", "dir", "root", "folder", "file"))
                or name in ("input", "out", "output", "src", "dst")):
            p.type = "path"
        act = kw.get("action")
        if isinstance(act, ast.Constant) and act.value in ("store_true", "store_false"):
            p.type = "bool"
            p.default = act.value == "store_false"
        if isinstance(act, ast.Constant) and act.value == "append":
            p.multiple = True
        d = kw.get("default")
        if isinstance(d, ast.Constant) and p.type != "bool":
            p.default = d.value
        r = kw.get("required")
        if isinstance(r, ast.Constant):
            p.required = bool(r.value)
        h = kw.get("help")
        if isinstance(h, ast.Constant) and isinstance(h.value, str):
            p.help = h.value
        params.append(p)

    if not params:
        notes.append("no argparse flags found — params drafted empty; "
                     "if the script uses sys.argv/click/typer, declare them by hand")
    return params, notes


def draft_deps(src: Path) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    for name in ("requirements.txt", "requirements.in"):
        f = src / name
        if f.exists():
            deps = [ln.strip() for ln in f.read_text().splitlines()
                    if ln.strip() and not ln.strip().startswith(("#", "-"))]
            return deps, notes
    py = src / "pyproject.toml"
    if py.exists():
        try:
            import tomllib
            data = tomllib.loads(py.read_text())
            return list(data.get("project", {}).get("dependencies", [])), notes
        except Exception:  # noqa: BLE001
            notes.append("pyproject.toml present but unparsable; env drafted empty")
    # last resort: top-level imports that aren't stdlib
    notes.append("no requirements file — deps guessed from imports, verify them")
    return _imports_guess(src), notes


# Import name != distribution name for a surprising number of packages.
IMPORT_TO_PIP = {
    "PIL": "pillow", "cv2": "opencv-python", "yaml": "pyyaml",
    "sklearn": "scikit-learn", "skimage": "scikit-image", "osgeo": "gdal",
    "serial": "pyserial", "dateutil": "python-dateutil", "OpenGL": "pyopengl",
    "fitz": "pymupdf", "docx": "python-docx", "pptx": "python-pptx",
    "bs4": "beautifulsoup4", "attr": "attrs", "zmq": "pyzmq",
    "usb": "pyusb", "Xlib": "python-xlib", "google": "protobuf",
}

# Packages whose pip wheels either don't exist or need system libraries.
# When one of these shows up, the honest answer is a conda-forge (pixi) env.
CONDA_PREFERRED = {"pdal", "gdal", "osgeo", "fiona", "rasterio", "geopandas",
                   "cartopy", "netCDF4", "h5py", "torch", "tensorflow", "cudf"}


def _imports_guess(src: Path) -> list[str]:
    found: set[str] = set()
    for p in src.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    import sys
    local = {p.stem for p in src.rglob("*.py")}
    # sys.stdlib_module_names is authoritative — no hand-maintained list.
    third_party = found - set(sys.stdlib_module_names) - local - {"sdtools"}
    return sorted(IMPORT_TO_PIP.get(m, m) for m in third_party)


# ----------------------------------------------------------------- writing

def wrap(cfg: Config, source: Path, name: str | None = None,
         entry: str | None = None, in_place: bool = False,
         environment: str | None = None, force: bool = False) -> WrapReport:
    source = source.expanduser()
    if not source.exists() or not source.is_dir():
        raise WrapError(
            f"{source} is not a readable directory on THIS machine. "
            "UNC paths (\\\\server\\share\\...) must be run from a machine "
            "that mounts the share — run `sdtools wrap` there, or copy the "
            "folder somewhere local first.")

    tool_name = (name or source.name).replace("_", "-").lower().strip("-")
    if not tool_name:
        raise WrapError(f"cannot derive a tool name from {source.name!r}; pass --name")
    slug = tool_name.replace("-", "_")
    tool_dir = cfg.tools_dir / slug
    if (tool_dir / "tool.yaml").exists() and not force:
        raise WrapError(f"{tool_dir}/tool.yaml already exists — "
                        f"pick a different --name, or pass --force to redraft it")

    entry_path = find_entry(source, entry)
    params, notes = draft_params(entry_path)
    deps, dep_notes = draft_deps(source)
    notes += dep_notes

    # Console-reserved names: keep the child script's flag, rename our side.
    from .discovery import RESERVED_PARAMS
    for p in params:
        if p.name in RESERVED_PARAMS:
            p.flag = f"--{p.name.replace('_', '-')}"
            p.name = f"{p.name}_arg"
            notes.append(f"param {p.flag} collides with a console option — "
                         f"exposed as --{p.name.replace('_', '-')} "
                         f"(child still receives {p.flag})")

    agentic = [f.name for f in source.iterdir()
               if f.is_file() and f.name in AGENTIC_FILES]
    if agentic:
        notes.append(
            f"agentic instruction file(s) {agentic} are NOT part of the tool — "
            "the console runs the scripts deterministically; prompts don't come along")

    tool_dir.mkdir(parents=True, exist_ok=True)
    conda_needed = sorted({d for d in deps if d.lower() in CONDA_PREFERRED})
    if conda_needed:
        notes.append(
            f"{', '.join(conda_needed)} need system libraries that pip wheels "
            f"often lack — for this stack use a pixi (conda-forge) env: set "
            f"`kind: pixi` in the drafted env.yaml and list the deps in "
            f"pixi.toml (see envs/ml-torch for the shape)")

    if in_place:
        run_ref = str(entry_path)
        src_note = f"# source referenced in place: {source}"
    else:
        dst = tool_dir / "src"
        if dst.exists() and force:
            shutil.rmtree(dst)
        shutil.copytree(source, dst, ignore=shutil.ignore_patterns(
            "__pycache__", ".git", "*.pyc", ".venv", "node_modules"))
        run_ref = str(Path("src") / entry_path.relative_to(source))
        src_note = f"# source copied from: {source}"

    env_name = environment
    env_file = None
    if env_name is None and deps:
        env_name = slug
        env_dir = envs_root(cfg) / env_name
        env_dir.mkdir(parents=True, exist_ok=True)
        env_file = env_dir / "env.yaml"
        if env_file.exists():
            # Never clobber an env spec you have already reviewed/edited.
            notes.append(f"{env_file.name} already exists — left untouched "
                         f"(delete it and re-wrap for a fresh draft)")
        else:
            env_file.write_text(
                "# drafted by `sdtools wrap` — review, then `sdtools env lock "
                f"{env_name}` and commit the lockfile\n"
                + yaml.safe_dump({"kind": "uv", "python": "3.12", "deps": deps},
                                 sort_keys=False))

    manifest = tool_dir / "tool.yaml"
    doc = {
        "name": tool_name,
        "version": "0.1.0",
        "summary": f"TODO: one line about what {tool_name} does.",
        "runtime": "python",
        "entry": run_ref,
        "environment": env_name or "system",
        "timeout_s": 3600,
        "params": [
            {k: v for k, v in {
                "name": p.name, "type": p.type,
                "required": p.required or None,
                "default": p.default,
                "multiple": p.multiple or None,
                "positional": p.positional or None,
                "help": p.help or None,
                "flag": p.flag,
            }.items() if v is not None}
            for p in params
        ],
    }
    manifest.write_text(
        "# drafted by `sdtools wrap` from "
        f"{entry_path.name} — REVIEW EVERY PARAM before relying on it\n"
        f"{src_note}\n" + yaml.safe_dump(doc, sort_keys=False))

    return WrapReport(tool_dir=tool_dir, manifest=manifest, entry=run_ref,
                      params=params, env_file=env_file, deps=deps,
                      agentic_ignored=agentic, notes=notes)
