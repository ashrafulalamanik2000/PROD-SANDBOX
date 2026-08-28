# TF1 PointCONV

This folder contains the legacy TensorFlow 1 PointCONV inference workflow.

Start here:

- [classification.md](tf1/classification.md)
- [TF1_TILED_INFERENCE_WORKFLOW.md](TF1_TILED_INFERENCE_WORKFLOW.md)

Main entrypoint:

- [classification.py](tf1/classification.py)

Default config:

- [inputconfig.yml](tf1/inputconfig.yml)

Legacy Docker wrapper:

- [Docker_Run_Classification.bat](tf1/Docker_Run_Classification.bat)

Current guidance:

- use TF1 when you need the legacy multiclass 6-class inference path
- use the tiled TF1 workflow for very large LAS inputs that need 0.1 m tiling and source-level merge-back
- use the TF2 layered workflow for current ground, vegetation, pole, and wire production-style inference
