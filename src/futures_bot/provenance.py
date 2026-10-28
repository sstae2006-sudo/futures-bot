"""Provenance helpers shared across `api/` and `research/`.

Small and dependency-free on purpose: `research/` must never import from
`api/` (the codebase's own documented layering rule -- `research/` is the
analysis library, `api/` is one of several callers of it, alongside
`tools/` scripts). `_git_commit` originally lived in `api/services.py`,
used only for a trained ML model's `app_version` provenance -- relocated
here, unchanged, so `research/optimizer.py`'s trial-provenance work (the
professional-research-pipeline plan's Phase 1d) can call the same
function without creating that forbidden import direction. `api/services.py`
now imports it from here instead of keeping its own copy.
"""

from __future__ import annotations

import hashlib
import subprocess
from typing import Optional, Sequence

from .models import Bar


def git_commit() -> Optional[str]:
    """Best-effort provenance for a trained model or research trial --
    never a reason the caller's own work should fail (no git installed,
    not a checkout, a shallow clone without HEAD, ...)."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def bars_dataset_version(product_code: str, resolution: str, bars: Sequence[Bar]) -> str:
    """Fingerprint for the exact bar series an optimizer trial (or any
    other bars-keyed research run) was scored against.

    A deliberately different convention from `research/ml/dataset.py`'s
    `_dataset_version` (which hashes trade *rows*, keyed by trade id +
    latest `created_at`) -- an optimizer trial is keyed by the input
    *bars*, which have no id/created_at of their own, so this hashes
    `(product_code, resolution, first bar timestamp, last bar timestamp,
    bar count)` instead. Two different bar series of the same length and
    endpoints (e.g. a resampled vs. a vendor-native series covering the
    identical range) would collide under this fingerprint -- acceptable
    for Phase 1d's purpose (detecting when a *materially different*
    dataset was used), not a cryptographic guarantee of exact content
    equality. Revisit if/when `market_data/resample.py` (Phase 2a) makes
    that collision a real ambiguity, not just a theoretical one.
    """
    if not bars:
        first_ts = last_ts = ""
    else:
        first_ts = bars[0].timestamp.isoformat()
        last_ts = bars[-1].timestamp.isoformat()
    digest = hashlib.sha256(
        f"{product_code}:{resolution}:{first_ts}:{last_ts}:{len(bars)}".encode()
    ).hexdigest()
    return digest[:16]
