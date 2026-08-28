#!/bin/bash

# Store existing PDAL env vars and set to this conda env
# so other PDAL installs don't pollute the environment
if [[ -n "$PDAL_DRIVER_PATH" ]]; then
    export _CONDA_SET_PDAL_PYTHON_DRIVER_PATH=$PDAL_DRIVER_PATH
fi

export PDAL_DRIVER_PATH=$_CONDA_SET_PDAL_PYTHON_DRIVER_PATH:D:bldbldattler-build_python-pdal_1772123266h_envibsite-packagespdal
