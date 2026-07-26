"""Phase 9: ML research workstation -- dataset assembly, chronological
evaluation, model training/persistence, prediction, and correlation
analysis. Heavy dependencies (scikit-learn, xgboost, torch, pandas) are
imported lazily inside each module's functions, never at package import
time, so the rest of the API can boot without the ``ml`` extra installed.
"""

from __future__ import annotations
