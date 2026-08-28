# Dockerize the Full AECON Pipeline (Pano Included)

Current `Dockerfile` runs 4 of 5 stages on Linux; Stage 4 pano shells out to
Windows `engine.exe`. These files make the whole pipeline Dockerizable.

## New / changed files

| File | Purpose |
|---|---|
| `pano_generator.py` | Pure-Python cubemap->equirect. Drop-in replacement for `engine.exe`'s `-p cubemap_to_panoramic`. Uses numpy + cv2 + Pillow only. |
| `Dockerfile.v2` | Adds `pano_generator.py` to the image and sets `AECON_ENGINE` env var. |
| `requirements.v2.txt` | Adds `Pillow>=9.0`. |

## One small patch needed in `stages.py`

The current `stage_panorama` hardcodes looking for `engine.exe` via `--engine`
or `%LOCALAPPDATA%\...`. Change one line so the engine arg can also be a
**command string** (e.g. `python3 /app/pano_generator.py`), controlled by
the `AECON_ENGINE` env var:

### Patch 1 — `aecon.py` (accept env var)

Around the `--engine` argparse line, add env-var fallback:

```python
parser.add_argument("--engine", default=os.environ.get("AECON_ENGINE"))
```

### Patch 2 — `stages.py`, `stage_panorama` (line ~235)

Replace:

```python
args = [engine_exe, "-p", "cubemap_to_panoramic",
        "--input_folder", os.path.dirname(csv_file),
        "--export_type", "jpg",
        "--rotation_type", "leica",
        "--output_folder", out_dir]
subprocess.run(args, check=False)
```

with:

```python
import shlex
engine_cmd = shlex.split(engine_exe) if isinstance(engine_exe, str) and " " in engine_exe else [engine_exe]
args = engine_cmd + ["-p", "cubemap_to_panoramic",
                     "--input_folder", os.path.dirname(csv_file),
                     "--export_type", "jpg",
                     "--rotation_type", "leica",
                     "--output_folder", out_dir]
subprocess.run(args, check=False)
```

That lets `engine_exe` be either a single binary path (as before) or a
command like `python3 /app/pano_generator.py` (Docker mode).

## Build & run

```bash
cd aecon_skill
# use the new files
mv Dockerfile Dockerfile.v1.bak
mv requirements.txt requirements.v1.bak
mv Dockerfile.v2 Dockerfile
mv requirements.v2.txt requirements.txt

docker build -t aecon:full .

docker run --rm \
  -v "/path/to/data":/data \
  aecon:full /data \
    --stages yaml,organize,metadata,pano,colorize \
    --crs EPSG:26917 --search-radius 45 --buffer 45 \
    --project-workers 2 --las-workers 4 --threads 8
```

No `--engine` flag needed — `AECON_ENGINE` env var in the Dockerfile
points at the local python pano generator.

## Validation checklist before trusting the output

1. Run `pano_generator.py` on one cubemap set on the host.
2. Compare one pano against the Solv3D output for the same frame:
   ```python
   from PIL import Image, ImageChops
   a = Image.open("solv3d_out.jpg")
   b = Image.open("python_out.jpg")
   diff = ImageChops.difference(a, b).getbbox()  # None = identical
   ```
   Small JPEG encoder differences are fine; the remap math should be
   pixel-identical because it's copied verbatim from Solv3D's source.
3. Run `colorize` against the Python-generated panos — RGB values on a
   handful of clipped LAS points should be within a few units of the
   Solv3D-pano run.

## What was dropped (and why it's safe)

* `speutils.license.check()` — removed. No runtime effect.
* `speutils.misc.check_values` — replaced by argparse choices.
* `speutils.misc.opk_to_rpy` — **dropped along with the `-encompass.csv`
  generation**. Downstream `stage_colorize` reads `Run_1_metadata.csv`
  (from Stage 3, which uses `Frot()` HRP->RPY, not the engine's
  post-processed CSV). Confirmed at `stages.py:328` — the encompass CSV
  is never referenced.
* `speutils.csv_tools.ENCOMPASS_HEADER` — only used by the dropped CSV.
* `speutils.multiprocess.MultiprocessingPool` — replaced by stdlib
  `concurrent.futures.ProcessPoolExecutor`.
