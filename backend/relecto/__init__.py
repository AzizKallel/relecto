"""Relecto: AI-assisted radiology reporting with a rules-first quality checker.

The checker reads report TEXT only. It never adds findings, measurements or
diagnoses: every issue points to words already in the report.
"""

__version__ = "0.4.0"


def check_report(*args, **kwargs):
    from .checker import check_report as _check
    return _check(*args, **kwargs)
