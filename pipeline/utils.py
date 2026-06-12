"""Shared, dependency-free helpers for the jobFind pipeline.

This is a leaf module: it imports nothing from the rest of the pipeline, so any
module can depend on it without creating an import cycle.
"""

from __future__ import annotations

import re
from typing import Any, Optional

NOT_SPECIFIED = "Not specified"


def clean_text(value: Any) -> str:
    """Normalize a value into one-line, display/CSV/JSON-safe text.

    Collapses whitespace (including non-breaking spaces) and returns
    ``NOT_SPECIFIED`` for empty input.
    """
    if value is None:
        return NOT_SPECIFIED
    text = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    return text or NOT_SPECIFIED


def parse_float(value: Any) -> Optional[float]:
    """Parse a numeric value, returning None instead of raising on bad input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def slugify_location(location: str) -> str:
    """Convert a location into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", location.lower()).strip("_")
    return slug or "location"
