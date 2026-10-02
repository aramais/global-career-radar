"""Self-contained browser UI for the local, persistent application CRM."""

from __future__ import annotations

from importlib.resources import files


def render_crm_page() -> str:
    """Return the CRM shell; records come from the same local server."""
    return files("job_intake.crm").joinpath("index.html").read_text(encoding="utf-8")
