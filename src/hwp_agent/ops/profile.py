"""Personal-data profile: auto-fill a Korean form from a saved data file.

Government forms (제안서 평가위원 등록신청서 …) ask the same person for the same
data over and over: 성명, 생년월일, 주소, 학력, 경력, 계좌번호. A *profile* captures
that standing data once (a JSON file) and maps it onto whatever slots a form
exposes (:func:`hwp_agent.ops.form.analyze_form`), so filling a new form is one
command instead of a manual transcription.

* Scalars (성명, 휴대폰, e-mail …) match a slot label through an alias table after
  Korean label normalisation.
* Repeated tabular sections (학력, 경력) map a profile list onto the blank rows
  under a recognised header (연도/학교/학위/전공, 기간/기관/직위/주요업무).
* Date slots / the ``@today`` sentinel resolve to today's date in ``YYYY. M. D.``.

JSON (stdlib) is used, not YAML — no extra dependency, same format as ``--map``.
The real profile is user data and is never committed; ``examples/profile.example.json``
is a redacted template to copy to ``~/.config/hwp-agent/profile.json``.
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import hwpx.tools.table_navigation as _tn
from hwpx.document import HwpxDocument

from .form import FormSpec, _rc, analyze_form, fill_form

# Default profile location (XDG-style).
DEFAULT_PROFILE = Path.home() / ".config" / "hwp-agent" / "profile.json"

# Normalised slot label -> dotted profile field.
ALIASES: dict[str, str] = {
    "성명": "name",
    "성명서명": "name",
    "이름": "name",
    "신청자": "name",
    "작성자": "name",
    "예금주": "name",
    "생년월일": "birthdate",
    "생일": "birthdate",
    "성별": "gender",
    "전문분야": "field",
    "전공분야": "field",
    "휴대폰": "mobile",
    "휴대전화": "mobile",
    "핸드폰": "mobile",
    "연락처": "mobile",
    "email": "work.email",
    "이메일": "work.email",
    "전자우편": "work.email",
    "메일": "work.email",
    "자택주소": "home.address",
    "자택전화번호": "home.phone",
    "직장명": "work.name",
    "소속": "work.name",
    "직위직급": "work.title",
    "직위": "work.title",
    "직급": "work.title",
    "직장주소": "work.address",
    "직장전화번호": "work.phone",
    "팩스": "work.fax",
    "계좌번호": "account.number",
    "금융기관명": "account.bank",
    "은행": "account.bank",
}

# Slot labels that should receive today's date when ``--date today`` is set.
DATE_LABELS = {"날짜", "작성일", "작성일자", "신청일", "신청일자", "작성년월일"}

# Column-header alias -> field key, for repeated 학력/경력 rows.
_COLUMNS: dict[str, dict[str, str]] = {
    "education": {
        "연도": "year", "년도": "year", "취득일": "year", "졸업연도": "year",
        "학교": "school", "학교명": "school", "출신학교": "school",
        "학위": "degree", "전공": "major", "학과": "major",
    },
    "career": {
        "기간": "period", "근무기간": "period",
        "기관": "org", "기관명": "org", "근무처": "org", "직장": "org",
        "직위": "title", "직급": "title",
        "주요업무": "duties", "담당업무": "duties", "업무": "duties",
    },
}


# --------------------------------------------------------------------------- #
# Profile data
# --------------------------------------------------------------------------- #
@dataclass
class Profile:
    data: dict
    source: Path | None = None

    def get(self, dotted_key: str | None) -> str | None:
        """Look up ``"work.email"`` style nested scalars (None if absent)."""
        if not dotted_key:
            return None
        node: object = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return str(node) if isinstance(node, (str, int, float)) else None

    @property
    def aliases(self) -> dict[str, str]:
        """Default aliases overlaid with the profile's own ``"aliases"`` block."""
        merged = dict(ALIASES)
        for label, field_name in (self.data.get("aliases") or {}).items():
            merged[normalize_label(label)] = field_name
        return merged

    def as_dict(self) -> dict:
        return {"source": str(self.source) if self.source else None, "data": self.data}


@dataclass
class ProfileMatch:
    slot: str
    field: str | None
    value: str | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProfileFillResult:
    filled: list[ProfileMatch] = field(default_factory=list)
    blank: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"filled": [m.as_dict() for m in self.filled], "blank": self.blank}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def resolve_profile_path(explicit: Path | str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("HWP_AGENT_PROFILE")
    return Path(env) if env else DEFAULT_PROFILE


def load_profile(path: Path | str | None = None) -> Profile:
    p = resolve_profile_path(path)
    if not p.is_file():
        raise FileNotFoundError(
            f"profile not found: {p}\n"
            "copy examples/profile.example.json there, or pass --profile <path>."
        )
    return Profile(data=json.loads(p.read_text(encoding="utf-8")), source=p)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def normalize_label(s: str) -> str:
    """Drop spaces/punctuation, NFC, casefold latin — so '성  명'/'e-mail :' match."""
    s = unicodedata.normalize("NFC", s or "")
    out = []
    for ch in s:
        if ch.isspace():
            continue
        if not (ch.isalnum()):  # strip colons, parens, dots, middots, hyphens …
            continue
        out.append(ch)
    return "".join(out).casefold()


def match_slot(
    slot_name: str, profile: Profile, *, aliases: dict[str, str] | None = None
) -> ProfileMatch:
    key = normalize_label(slot_name)
    table = aliases if aliases is not None else profile.aliases
    field_name = table.get(key)
    if field_name is None and profile.get(f"extra.{key}") is not None:
        field_name = f"extra.{key}"
    return ProfileMatch(slot=slot_name, field=field_name, value=profile.get(field_name))


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def kdate(d: date) -> str:
    """Korean form date, e.g. ``2026. 6. 4.`` (no zero pad, space after each dot)."""
    return f"{d.year}. {d.month}. {d.day}."


# --------------------------------------------------------------------------- #
# Repeated tabular sections (학력 / 경력)
# --------------------------------------------------------------------------- #
def map_repeated_section(
    doc: HwpxDocument, profile: Profile, section: str
) -> dict[str, str]:
    """Map a profile list (education/career) onto blank rows under a header.

    Returns ``cell:<table>:<row>:<col>`` → value targets, aligned by matching the
    table's header labels to the section's column aliases.
    """
    items = profile.data.get(section)
    if not isinstance(items, list) or not items:
        return {}
    col_aliases = _COLUMNS[section]
    targets: dict[str, str] = {}
    for it in _tn._collect_document_tables(doc):
        ti, table = it.table_index, it.table
        try:
            nrows, ncols = _rc(table)
        except Exception:
            continue
        # find a header row whose cells map to this section's columns
        header_row = col_map = None
        for r in range(nrows):
            mapping: dict[int, str] = {}
            for c in range(ncols):
                try:
                    label = normalize_label(table.cell(r, c).text or "")
                except Exception:
                    continue
                if label in col_aliases:
                    mapping[c] = col_aliases[label]
            if len(mapping) >= 2:  # at least two recognised columns ⇒ a header
                header_row, col_map = r, mapping
                break
        if header_row is None:
            continue
        for i, entry in enumerate(items):
            row = header_row + 1 + i
            if row >= nrows:
                break
            for c, field_name in col_map.items():
                val = entry.get(field_name) if isinstance(entry, dict) else None
                if val:
                    targets[f"cell:{ti}:{row}:{c}"] = str(val)
        return targets  # first matching table only
    return targets


# --------------------------------------------------------------------------- #
# Top-level fill
# --------------------------------------------------------------------------- #
def build_mapping(
    spec: FormSpec, doc: HwpxDocument, profile: Profile, *, date_today: bool
) -> tuple[dict[str, str], list[ProfileMatch], list[str]]:
    """Build the fill mapping + report from a form spec and a profile."""
    aliases = profile.aliases
    mapping: dict[str, str] = {}
    matches: list[ProfileMatch] = []
    blank: list[str] = []
    today = kdate(date.today())
    date_keys = {normalize_label(x) for x in DATE_LABELS}

    # Repeated sections first: they own their cells and column-header labels, so a
    # career "직위" column isn't also scalar-matched to work.title.
    repeated: dict[str, str] = {}
    column_labels: set[str] = set()
    for section in ("education", "career"):
        if isinstance(profile.data.get(section), list) and profile.data[section]:
            repeated.update(map_repeated_section(doc, profile, section))
            column_labels |= {normalize_label(k) for k in _COLUMNS[section]}

    for slot in spec.slots:
        if slot.kind not in ("cell", "placeholder"):
            continue
        key = slot.cell_path or slot.locator
        label = normalize_label(slot.name)
        if key in repeated or label in column_labels:
            continue  # owned by a repeated section — don't scalar-fill it
        m = match_slot(slot.name, profile, aliases=aliases)
        if m.value is not None and (m.value or "") != "@today":
            mapping[key] = m.value
            matches.append(m)
        elif date_today and label in date_keys:
            mapping[key] = today
            matches.append(ProfileMatch(slot.name, "@today", today))
        else:
            blank.append(slot.name)

    mapping.update(repeated)
    # resolve any "@today" sentinels left in profile-sourced values
    for k, v in list(mapping.items()):
        if v == "@today":
            mapping[k] = today
    return mapping, matches, blank


def fill_from_profile(
    hwpx_path: Path | str,
    profile_path: Path | str | None = None,
    *,
    output: Path | str | None = None,
    date: str | None = None,
    precise: bool = True,
) -> ProfileFillResult:
    """Analyse a form, auto-map a profile onto its slots, fill, and report."""
    profile = load_profile(profile_path)
    spec = analyze_form(hwpx_path, precise=precise)
    doc = HwpxDocument.open(str(hwpx_path))
    mapping, matches, blank = build_mapping(
        spec, doc, profile, date_today=(date == "today")
    )
    fill_form(hwpx_path, mapping, output=output, precise=precise)
    return ProfileFillResult(filled=matches, blank=blank)
