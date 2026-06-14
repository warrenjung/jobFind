"""Shared, dependency-free helpers for the jobFind pipeline.

This is a leaf module: it imports nothing from the rest of the pipeline, so any
module can depend on it without creating an import cycle.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

NOT_SPECIFIED = "Not specified"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to ``path`` atomically.

    Writes to a temporary file in the same directory and then ``os.replace``s
    it into place, so an interrupted write (crash, Ctrl-C, power loss) can never
    leave a half-written or truncated file at ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically (see ``atomic_write_bytes``)."""
    atomic_write_bytes(path, text.encode("utf-8"))


def safe_resume_filename(name: Any, allowed_exts: Any) -> Optional[str]:
    """Return a sanitized, allowlist-checked resume filename, or None.

    Strips any directory components (path traversal) and null bytes, keeps only
    safe characters in the stem, and requires the lowercased extension to be in
    ``allowed_exts``.
    """
    raw = os.path.basename(str(name or "")).replace("\x00", "").strip()
    if not raw:
        return None
    stem, ext = os.path.splitext(raw)
    ext = ext.lower()
    if ext not in allowed_exts:
        return None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem) or "resume"
    return f"{stem}{ext}"


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
