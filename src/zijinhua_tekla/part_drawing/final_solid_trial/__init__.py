"""Independent final-solid geometry and expression trial.

This package intentionally does not import the legacy geometry or expression
pipeline.  It consumes a saved ``teklaNativeEvidence.v1`` snapshot only.
"""

from .analysis import analyze_snapshot, write_trial

__all__ = ["analyze_snapshot", "write_trial"]
