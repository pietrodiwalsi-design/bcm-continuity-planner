"""HTML-to-PDF rendering for the web workshop tool's BIA/BCP/CMT exports.

Uses WeasyPrint (pure-Python-callable, no external binary/wkhtmltopdf
dependency, works from a plain `pip install weasyprint` in this
environment — verified during the Phase-6/web-workshop build) against
Jinja2 templates under `templates/pdf/`. Contains **no BIA/BCP/CMT
business logic** of its own — every value rendered into a template is
computed elsewhere (`bia_engine.py`, `bcp_generator.py`,
`crisis_management.py`) and passed in as plain dicts/lists by
`app.py`'s route handlers. This module's only job is HTML template
rendering -> PDF bytes.

`[project.optional-dependencies] web` in pyproject.toml pins `weasyprint`
alongside `fastapi`/`jinja2`/`uvicorn` — see pyproject.toml for the exact
dependency group.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

__all__ = ["render_bia_pdf", "render_bcp_pdf", "render_cmt_pdf"]

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "pdf"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _render_html(template_name: str, **context: Any) -> str:
    template = _env.get_template(template_name)
    return template.render(generated_at=datetime.datetime.now(datetime.timezone.utc), **context)


def _html_to_pdf(html: str) -> bytes:
    """Renders an HTML string to PDF bytes. `base_url` is set to the
    templates directory so relative asset references (none currently used,
    but kept for forward-compatibility) would resolve correctly.
    """
    return HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()


def render_bia_pdf(
    organization: dict[str, Any],
    activities: list[dict[str, Any]],
) -> bytes:
    """Renders the Business Impact Analysis export PDF.

    `activities` is a list of dicts, each shaped:
    {"activity": <activities row>, "bia_assessments": [...],
     "recovery_strategies": [...], "gap_analyses": [...], "spofs": [...]}
    — assembled by app.py from bia_engine reads, never queried directly
    here.
    """
    html = _render_html("bia_report.html", organization=organization, activities=activities)
    return _html_to_pdf(html)


def render_bcp_pdf(
    organization: dict[str, Any],
    plan: dict[str, Any],
    action_steps: list[dict[str, Any]],
    bau_phases: list[dict[str, Any]],
) -> bytes:
    """Renders a single Business Continuity Plan (bc_plans row + its
    bcp_action_steps + bau_return_procedures) export PDF.
    """
    html = _render_html(
        "bcp_report.html",
        organization=organization,
        plan=plan,
        action_steps=action_steps,
        bau_phases=bau_phases,
    )
    return _html_to_pdf(html)


def render_cmt_pdf(
    organization: dict[str, Any],
    cmt_roles: list[dict[str, Any]],
    escalation_triggers: list[dict[str, Any]],
) -> bytes:
    """Renders the Crisis Management Team plan export PDF: the full CMT
    roster (roles + primary/alternate contacts) and the escalation trigger
    table for one organization.
    """
    html = _render_html(
        "cmt_report.html",
        organization=organization,
        cmt_roles=cmt_roles,
        escalation_triggers=escalation_triggers,
    )
    return _html_to_pdf(html)
