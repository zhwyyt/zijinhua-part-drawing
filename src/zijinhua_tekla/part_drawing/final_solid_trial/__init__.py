"""Independent final-solid geometry and expression trial.

This package never invokes the legacy geometry audit, classification or cutting
pipeline. It reuses documented pure expression and approved supplemental
evidence helpers, and consumes saved ``teklaNativeEvidence.v1`` snapshots only.
"""

from .analysis import analyze_snapshot, write_trial

__all__ = ["analyze_snapshot", "write_trial"]
