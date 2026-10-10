"""Consumer-side claim adjudication. No network access or implicit trust roots."""

from .core import CaseError, adjudicate

__all__ = ["CaseError", "adjudicate"]
