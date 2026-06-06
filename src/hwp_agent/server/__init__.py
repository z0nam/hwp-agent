"""Self-hosted HTTP server for hwp-agent (`hwp-agent serve`).

One FastAPI app, meant to run on a machine the user controls (e.g. a Mac mini —
the same box that already hosts their Slack bots), so documents never leave their
own hardware. It exposes three things over one port:

* a **dead-simple web upload page** (link → drag a form → download the filled
  result) — the lowest-friction, no-AI, no-install path for non-technical users;
* a **REST API** reusing the ``ops`` core (convert / analyze / fill / profile);
* an **OpenAPI schema** (FastAPI's ``/openapi.json``) usable as a **ChatGPT
  custom-GPT Action**.

Because the converter jar runs here (Java present on the host), ``.hwp`` → ``.hwpx``
conversion works server-side — unlike the AI code-execution sandboxes.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app():  # lazy import so the optional [serve] deps aren't required at import time
    from .app import create_app as _create

    return _create()
