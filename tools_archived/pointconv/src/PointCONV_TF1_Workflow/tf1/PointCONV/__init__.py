# Mark this directory as a proper Python package so PointCONV.py's
# relative imports (`from .PointCONV_Segment import ...`) work in
# joblib worker processes.
#
# 2026-05-24: added during Verizon LA Zone 10 chain recovery — Stage 1
# Phase 2 (TF1 inference) crashed in joblib worker with
#   "attempted relative import with no known parent package"
# because Python 3 implicit namespace packages don't always survive
# joblib's loky spawn-style worker re-import. Adding an explicit
# __init__.py makes PointCONV a real package and the relative import
# resolves correctly in workers.
