# Post-Processing Helpers

This folder contains post-processing tools that combine model outputs into review-ready LAS files.

## TF1 Tiled Inference Merge

[merge_tf1_tile_predictions.py](post_processing/merge_tf1_tile_predictions.py) merges legacy TF1 PointCONV tile outputs back to one 0.1 m thinned LAS per original source file.

The merge step:

| Step | Purpose |
| --- | --- |
| Read tile manifest | Recover source-to-tile ownership from preprocessing. |
| Match TF1 raw outputs | Map `*_raw.las` predictions back to the tile input points. |
| Keep core points only | Avoid duplicate boundary votes from overlapping tile halos. |
| Write combined LAS | Store merged class predictions, probability, and vote count. |

See [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md) for the full workflow.
