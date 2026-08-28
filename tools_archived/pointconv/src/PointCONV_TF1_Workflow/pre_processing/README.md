# Preprocessing Helpers

This folder contains preprocessing tools that prepare LAS files before model inference.

## TF1 Tiled Inference

[build_tf1_inference_tiles.py](pre_processing/build_tf1_inference_tiles.py) builds 0.1 m thinned, overlapping LAS tiles for the legacy TF1 PointCONV pipeline.

It writes:

| Output | Purpose |
| --- | --- |
| `source_thinned/` | One thinned LAS per source file. |
| `preprocessed_tiles/` | Tile LAS files used as `tf1/classification.py --input_folder`. |
| `tile_indices/` | Source-thinned index and core-mask sidecars for merging predictions. |
| `manifests/` | JSON and CSV manifests for auditing and post-processing. |

See [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md) for the full workflow.
