"""Markdown links ↔ Hangul hyperlink fields (issue #27).

A Hangul hyperlink is a field: ``<hp:ctrl><hp:fieldBegin type="HYPERLINK">`` with the
target in its ``Command`` parameter, the visible text as ordinary ``<hp:t>``, then a
``<hp:ctrl><hp:fieldEnd beginIDRef=…>`` closing it — one run holding begin ctrl,
text, end ctrl, as Hangul writes its 목차 links (``tests/fixtures/sample_big_ref.hwpx``).

The URL form copies a real document's link (JRI 그룹웨어 기안문 서식, which opens
in Hangul): ``Prop`` 9 and ``Command`` = the URL with ``\\ : ;`` backslash-escaped
plus ``;1;0;2;`` (``http\\://host/p.do?a=b;1;0;2;``). That sample also escapes
``?``, but Hangul keeps such a ``\\?`` literally in the target it exports (verified
via hwp2pdf → docx, 2026-10-15), so ``?`` is left as is. python-hwpx's own
``add_hyperlink`` only sets ``name=url`` with no parameters, which Hangul does not
treat as a link — hence this module.

* :func:`link_pieces` splits text into plain/link pieces — ``[text](url)`` and bare
  ``http(s)://`` URLs (``<url>`` autolinks too);
* :func:`link_run_xml` builds one ``<hp:run>`` carrying a link;
* :func:`hyperlink_url` reads a field's target back (``extract``);
* :func:`audit_links` counts linked fields, unmatched field pairs and bare URL text
  left unlinked (``check``).
"""

from __future__ import annotations

import itertools
import random
import re
import string

_PUNCT = set(string.punctuation)


def mask_escapes(text: str, *, math: bool = True) -> str:
    r"""Mask CommonMark backslash-escaped ASCII punctuation with private-use characters.

    CommonMark rule: backslash followed by an ASCII punctuation character
    (! " # $ % & ' ( ) * + , - . / : ; < = > ? @ [ \ ] ^ _ ` { | } ~) represents
    that literal character and does not trigger Markdown formatting (headings,
    bullets, ordered lists, pipe tables, bold/italic, links, etc.).

    Backslash followed by a non-punctuation character (e.g. C:\Users, \n) is kept.
    Inside code spans (`...`), backslashes are preserved verbatim and not unescaped.
    With *math*, LaTeX commands inside math spans ($...$, $$...$$) are preserved.
    """
    i = 0
    n = len(text)
    out: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 < n and text[i + 1] in _PUNCT:
                out.append("\ue002" + chr(0xE100 + ord(text[i + 1])))
                i += 2
            else:
                out.append("\\")
                i += 1
        elif ch == "`":
            # Count opening backticks
            j = i
            while j < n and text[j] == "`":
                j += 1
            fence_len = j - i
            fence = "`" * fence_len
            close_idx = text.find(fence, j)
            if close_idx != -1:
                # Code span: keep verbatim without unescaping backslashes
                out.append(text[i : close_idx + fence_len])
                i = close_idx + fence_len
            else:
                out.append(fence)
                i = j
        elif math and ch == "$":
            # Display math $$...$$
            if i + 1 < n and text[i + 1] == "$":
                close_idx = text.find("$$", i + 2)
                if close_idx != -1:
                    out.append(text[i : close_idx + 2])
                    i = close_idx + 2
                    continue
            # Inline math $...$
            if i + 1 < n and text[i + 1] not in (" ", "\t", "\n", "$"):
                blank_idx = text.find("\n\n", i + 1)
                k = i + 1
                found_close = -1
                while k < n and (blank_idx == -1 or k < blank_idx):
                    if (
                        text[k] == "$"
                        and text[k - 1] != "\\"
                        and text[k - 1] not in (" ", "\t", "\n")
                    ):
                        if k + 1 >= n or (text[k + 1] != "$" and not text[k + 1].isdigit()):
                            found_close = k
                            break
                    k += 1
                if found_close != -1:
                    out.append(text[i : found_close + 1])
                    i = found_close + 1
                    continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_UNMASK_RE = re.compile(r"\ue002([\ue121-\ue17e])")


def unmask_escapes(text: str) -> str:
    """Restore private-use characters mapped by :func:`mask_escapes` to ASCII punctuation."""
    if not text:
        return text
    return _UNMASK_RE.sub(lambda m: chr(ord(m.group(1)) - 0xE100), text)


_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

#: Hangul's field id for hyperlinks ('%hlk' as a 32-bit tag), as it writes it
HYPERLINK_FIELD_ID = "627600491"

#: ``[text](target)`` — target needs a scheme (http:, mailto:, …) so a relative path
#: or a bracketed aside stays literal; an optional ``"title"`` is dropped. Not an
#: image (``![…]``) and not a footnote reference (``[^id]``).
_MD_LINK_RE = re.compile(
    r"(?<!!)\[(?!\^)([^\]\n]+)\]\(\s*<?"
    r"([A-Za-z][A-Za-z0-9+.\-]*(?::|\ue002\ue13a)(?:[^\s()<>]|\([^\s()<>]*\))+)>?"
    r"(?:\s+\"[^\"]*\")?\s*\)"
)
#: a bare ``http(s)://`` URL (greedy up to whitespace / CJK punctuation, then
#: trimmed by :func:`_trim_url`; not the target of an image ``![…](url)``) or a
#: ``<url>`` autolink (group 1) — neither form as an image destination ``![…](…)``
_BARE_URL_RE = re.compile(
    r"(?<!\]\()<(https?://[^\s<>]+)>"
    r"|(?<![\w/@.\-])(?<!\]\()(?<!\]\(<)https?://[^\s<>\"「」『』（）〈〉《》，。、·“”‘’]+"
)
#: punctuation that ends a sentence rather than the URL
_TRAILING = ".,;:!?'\"*"
#: the host part (scheme://host[:port]) — ASCII only, so ``https://x.kr에서`` stops
_HOST_RE = re.compile(r"https?://[A-Za-z0-9\-._~%:@\[\]]+")
#: a trailing non-ASCII run glued to an ASCII character (``…/page에서``) is a Korean
#: particle, not path; one that starts a segment (``…/법령/개인정보보호법``) is path.
#: Only applied to host/path URLs — in a query or fragment (``?q=제주``, ``#섹션``) a
#: Korean value is real data, so it's kept (PR #28 review)
_GLUED_TAIL_RE = re.compile(r"(?<=[A-Za-z0-9\-._~%)\]])[^\x00-\x7f]+$")


def _trim_url(url: str) -> str:
    """Cut a greedy bare-URL match back to the URL itself.

    Non-ASCII may appear in the path (law.go.kr's ``/법령/…``) but not in the host;
    a trailing Korean run glued to ASCII in the host/path is a particle (not in a
    query/fragment, where it's a value); sentence punctuation is
    dropped; ``)``/``]`` are kept only when balanced."""
    host = _HOST_RE.match(url)
    if host is None:
        return ""
    if host.end() < len(url) and url[host.end()] not in "/?#":
        url = url[: host.end()]
    while url:
        if "?" not in url and "#" not in url:
            url = _GLUED_TAIL_RE.sub("", url)
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        elif last == "]" and url.count("[") < url.count("]"):
            url = url[:-1]
        else:
            break
    return url


def link_pieces(text: str, *, unmask: bool = True) -> list[tuple[str, str | None]]:
    """Split *text* into ``(text, url)`` pieces; ``url`` is None for plain text.

    ``[표시](https://…)`` → ``("표시", "https://…")``; a bare URL links to itself.
    Text without links comes back as a single plain piece."""
    text = mask_escapes(text)
    pieces: list[tuple[str, str | None]] = []

    def plain(s: str) -> None:
        pos = 0
        for m in _BARE_URL_RE.finditer(s):
            if m.group(1):  # <url> autolink: brackets are markup, not text
                url, end = m.group(1), m.end()
            else:
                url = _trim_url(m.group(0))
                end = m.start() + len(url)
            if not url or url.endswith("://"):
                continue
            if m.start() > pos:
                pieces.append((s[pos : m.start()], None))
            pieces.append((url, url))
            pos = end
        if pos < len(s):
            pieces.append((s[pos:], None))

    pos = 0
    for m in _MD_LINK_RE.finditer(text):
        plain(text[pos : m.start()])
        pieces.append((m.group(1), m.group(2)))
        pos = m.end()
    plain(text[pos:])
    # merge adjacent plain pieces (a skipped bare-URL match splits them)
    merged: list[tuple[str, str | None]] = []
    for t, u in pieces:
        if u is None and merged and merged[-1][1] is None:
            merged[-1] = (merged[-1][0] + t, None)
        else:
            merged.append((t, u))
    if unmask:
        return [
            (unmask_escapes(t), unmask_escapes(u) if u is not None else None)
            for t, u in merged
        ] or [(unmask_escapes(text), None)]
    return merged or [(text, None)]



def has_links(text: str) -> bool:
    return any(u is not None for _, u in link_pieces(text))


def rendered_text(text: str) -> str:
    """*text* as drawn — ``[표시](url)`` shows only ``표시`` (for width estimates)."""
    return "".join(t for t, _ in link_pieces(text))


#: Minimum unbroken (whitespace-free) character length in a hyperlink's visible
#: label that triggers left alignment under ``--link-align auto`` (issue #35).
DEFAULT_LINK_ALIGN_THRESHOLD = 30


def should_left_align(
    text: str,
    mode: str = "auto",
    threshold: int = DEFAULT_LINK_ALIGN_THRESHOLD,
) -> bool:
    """Return True if paragraph *text* should be left-aligned under *mode*.

    - ``"keep"``: never re-align (preserve current behavior).
    - ``"left"``: left-align if *text* contains any hyperlink.
    - ``"auto"``: left-align if any hyperlink's visible text contains a
      whitespace-free sequence of *threshold* or more characters. Short labels
      like ``[짧은 한글](https://...)`` do not trigger re-alignment.
    """
    if mode == "keep":
        return False
    pieces = link_pieces(text)
    if not any(u is not None for _, u in pieces):
        return False
    if mode == "left":
        return True
    if mode == "auto":
        for label, url in pieces:
            if url is None:
                continue
            if any(len(chunk) >= threshold for chunk in label.split()):
                return True
    return False


def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def command_for_url(url: str) -> str:
    """Hangul's hyperlink ``Command`` string for a URL target.

    Field commands are ``;``-separated with ``\\`` escaping, so ``\\`` ``:`` ``;`` in
    the URL are backslash-escaped (``https\\://…``) and ``;1;0;2;`` follows, as in
    Hangul-saved URL links."""
    esc = url.replace("\\", "\\\\")
    for ch in ":;":
        esc = esc.replace(ch, "\\" + ch)
    return f"{esc};1;0;2;"


def url_from_command(command: str) -> str | None:
    """Inverse of :func:`command_for_url`; None for an in-document target (``?#…``)."""
    out: list[str] = []
    it = iter(command)
    for ch in it:
        if ch == "\\":
            out.append(next(it, ""))
        elif ch == ";":
            break
        else:
            out.append(ch)
    target = "".join(out)
    if not target or target.startswith("?"):
        return None
    return target


_ids = itertools.count(random.randint(1_000_000_000, 1_900_000_000))


def new_field_id() -> int:
    """A fresh fieldBegin ``id`` (Hangul uses large random-looking integers)."""
    return next(_ids)


def field_begin_xml(url: str, field_id: int) -> str:
    """``<hp:ctrl><hp:fieldBegin type="HYPERLINK">…`` for *url* (no namespace decl)."""
    return (
        f'<hp:ctrl><hp:fieldBegin id="{field_id}" type="HYPERLINK" name="" '
        f'editable="0" dirty="1" zorder="-1" fieldid="{HYPERLINK_FIELD_ID}" metaTag="">'
        f'<hp:parameters cnt="2" name="">'
        f'<hp:integerParam name="Prop">9</hp:integerParam>'
        f'<hp:stringParam name="Command" xml:space="preserve">'
        f"{_escape_xml(command_for_url(url))}</hp:stringParam>"
        f"</hp:parameters></hp:fieldBegin></hp:ctrl>"
    )


def field_end_xml(field_id: int) -> str:
    return (
        f'<hp:ctrl><hp:fieldEnd beginIDRef="{field_id}" '
        f'fieldid="{HYPERLINK_FIELD_ID}"/></hp:ctrl>'
    )


def link_inner_xml(text: str, url: str) -> str:
    """Begin ctrl + text + end ctrl — the content of a link run."""
    fid = new_field_id()
    return f"{field_begin_xml(url, fid)}<hp:t>{_escape_xml(text)}</hp:t>{field_end_xml(fid)}"


def link_run_xml(text: str, url: str, char) -> str:
    """One ``<hp:run>`` (namespace declared) carrying a hyperlink."""
    run_char = f' charPrIDRef="{char}"' if char is not None else ""
    return f'<hp:run xmlns:hp="{_HP}"{run_char}>{link_inner_xml(text, url)}</hp:run>'


def hyperlink_url(field_begin_el) -> str | None:
    """The URL a ``<hp:fieldBegin type="HYPERLINK">`` points to, else None.

    Reads the ``Command`` parameter (Hangul's form); falls back to the ``name``
    attribute, where python-hwpx's ``add_hyperlink`` stores it."""
    if field_begin_el.get("type") != "HYPERLINK":
        return None
    for sp in field_begin_el.iter(f"{{{_HP}}}stringParam"):
        if sp.get("name") == "Command":
            return url_from_command(sp.text or "")
    name = field_begin_el.get("name") or ""
    return name if re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:", name) else None


def audit_links(section_root) -> dict:
    """Count hyperlink fields, unmatched field pairs, and unlinked bare URLs.

    Walks the section in document order; text inside an open hyperlink field is
    linked, any other ``http(s)://`` in ``<hp:t>`` is a bare URL."""
    begins: dict[str, str] = {}  # id -> type
    ends: list[str] = []
    open_links: list[str] = []
    hyperlinks = 0
    bare: list[str] = []
    for el in section_root.iter():
        tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
        if tag == "fieldBegin":
            fid = el.get("id") or ""
            begins[fid] = el.get("type") or ""
            if el.get("type") == "HYPERLINK":
                hyperlinks += 1
                open_links.append(fid)
        elif tag == "fieldEnd":
            ref = el.get("beginIDRef") or ""
            ends.append(ref)
            if ref in open_links:
                open_links.remove(ref)
        elif tag == "t" and not open_links:
            text = "".join(el.itertext())
            bare += [u for _, u in link_pieces(text) if u is not None]
    end_set = set(ends)
    return {
        "hyperlinks": hyperlinks,
        "unclosed": sorted(f for f in begins if f not in end_set),
        "orphan_ends": sorted(r for r in end_set if r not in begins),
        "bare_urls": bare,
    }


def audit_package_links(path) -> dict:
    """:func:`audit_links` over every section of the HWPX package at *path*.

    Returns ``{"hyperlinks": n, "bare_urls": [...], "broken": [(section, detail)]}``
    — *broken* lists fieldBegin/fieldEnd pairs that don't match (any field type)."""
    import zipfile

    from lxml import etree

    out: dict = {"hyperlinks": 0, "bare_urls": [], "broken": []}
    with zipfile.ZipFile(path) as zf:
        names = sorted(
            (n for n in zf.namelist() if re.match(r"^Contents/section\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
        )
        for name in names:
            try:
                root = etree.fromstring(zf.read(name))
            except etree.XMLSyntaxError:
                continue
            a = audit_links(root)
            out["hyperlinks"] += a["hyperlinks"]
            out["bare_urls"] += a["bare_urls"]
            if a["unclosed"]:
                out["broken"].append((name, "fieldBegin without fieldEnd: id "
                                      + ", ".join(a["unclosed"])))
            if a["orphan_ends"]:
                out["broken"].append((name, "fieldEnd without fieldBegin: beginIDRef "
                                      + ", ".join(a["orphan_ends"])))
    return out
