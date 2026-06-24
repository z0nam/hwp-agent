"""Resolve the template ``write`` fills when none is given explicitly.

Resolution order (first hit wins) — mirrors :mod:`.profile`'s convention:

1. an explicit ``--template`` path,
2. ``$HWP_AGENT_TEMPLATE``,
3. ``~/.config/hwp-agent/template.hwpx`` (a user-installed house default),
4. the **bundled** package default (``hwp_agent/assets/default-template.hwpx``).

The bundled default is a content-centred report template: an OUTLINE heading
ladder (Ⅰ. / 1. / 1)), a ￭/- bullet ladder (``AI:BULLET_1``/``AI:BULLET_2``), a
``{{body}}`` insertion marker, a ``{{table_template}}`` house-style table, and
``AI:INSTRUCTION`` guidance paragraphs (stripped on write). So ``hwp-agent write
content.md`` works with no template flag at all.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

#: a user-installed default — drop any .hwpx here to override the bundled one
USER_DEFAULT = Path.home() / ".config" / "hwp-agent" / "template.hwpx"
_BUNDLED_REL = "assets/default-template.hwpx"


def bundled_template_path() -> Path:
    """Filesystem path to the template shipped inside the package."""
    ref = files("hwp_agent").joinpath(_BUNDLED_REL)
    # normal installs (pipx / uv tool / editable) expose a real path directly.
    try:
        p = Path(str(ref))
        if p.is_file():
            return p
    except Exception:  # pragma: no cover - non-filesystem loader
        pass
    # zip-imported install: extract once to a stable cache path
    import tempfile

    data = ref.read_bytes()  # type: ignore[union-attr]
    cache = Path(tempfile.gettempdir()) / "hwp-agent" / "default-template.hwpx"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file() or cache.stat().st_size != len(data):
        cache.write_bytes(data)
    return cache


def resolve_template_path(explicit: Path | str | None) -> Path:
    """The template to use, following the resolution order above.

    Only an *explicit* path or ``$HWP_AGENT_TEMPLATE`` is returned unverified
    (the caller surfaces a clear FileNotFoundError on open); the user-config and
    bundled defaults are returned only when they actually exist.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("HWP_AGENT_TEMPLATE")
    if env:
        return Path(env)
    if USER_DEFAULT.is_file():
        return USER_DEFAULT
    return bundled_template_path()


def describe_template_source(explicit: Path | str | None) -> str:
    """Human label for *where* the resolved template came from (for CLI notes)."""
    if explicit:
        return "explicit"
    if os.environ.get("HWP_AGENT_TEMPLATE"):
        return "$HWP_AGENT_TEMPLATE"
    if USER_DEFAULT.is_file():
        return str(USER_DEFAULT)
    return "bundled default"
