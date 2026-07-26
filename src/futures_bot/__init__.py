"""
Futures Bot package initialization.
"""

from .strategy.opening_range_breakout import OpeningRangeBreakout

__version__ = "0.7.0"

__all__ = [
    "OpeningRangeBreakout",
    "__version__",
]