"""Estimate the box Hangul draws for an equation script (issue #5).

Hangul lays an ``<hp:equation>`` out from its script when the object is edited, but
when a file is opened and printed it trusts the stored ``<hp:sz>`` (width/height)
and ``baseLine`` to reserve room in the line — a box that is too narrow makes the
following text overprint the formula (hwp2pdf, 2026-10-16). Files written by
``write`` have never been through the equation editor, so the box is estimated
here with a small TeX-like box model: fractions stack, sub/superscripts shrink,
big operators carry their limits above/below, matrices are grids.

All sizes are in em (1 em = the equation's ``baseUnit``). The constants were fitted
against 130 equations Hangul itself laid out (their stored sizes), see
``tests/test_equation.py::test_layout_matches_hangul_samples``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# glyph advance widths (em), Hangul's HancomEQN at baseUnit 1
_W_LOWER = 0.50
_W_UPPER = 0.66
_W_DIGIT = 0.50
_W_WIDE = 1.00  # Hangul / CJK
_W_PUNCT = 0.30  # ( ) , . ; : | [ ]
_W_BINOP = 0.90  # + - = < > … with Hangul's own surrounding space
_W_SYMBOL = 0.80  # a command that draws one glyph (cdots, oplus …)
_W_GREEK = 0.55
_GREEK = set(
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa "
    "lambda mu nu xi omicron pi varpi rho varrho sigma varsigma tau upsilon phi "
    "varphi chi psi omega".split()
)
_SCRIPT = 0.62  # sub/superscript scale
_ASC, _DESC = 0.80, 0.22  # one plain line

_BINOPS = {
    "+", "-", "=", "<", ">", "<=", ">=", "!=", "+-", "-+", "->", "<-", "<->", "=>",
    "times", "cdot", "div", "sim", "approx", "simeq", "equiv", "cong", "propto",
    "in", "notin", "subset", "supset", "subseteq", "supseteq", "owns", "/",
    "LARROW", "RARROW", "LRARROW", "geq", "leq",
    "±", "∓", "×", "÷", "≤", "≥", "≠", "≈", "≡", "∈", "∉", "⊂", "⊃", "⊆", "⊇",
    "⇒", "⇐", "⇔", "→", "←", "↔",
}
_BIG = {"sum", "prod", "coprod", "union", "inter", "lim", "Lim", "max", "min"}
_INTEGRALS = {"int", "oint", "dint", "tint", "odint", "otint"}
_FUNC_WORDS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "coth",
    "arcsin", "arccos", "arctan", "exp", "log", "ln", "lg", "sup", "det", "gcd",
    "deg", "dim", "ker", "hom", "arg", "mod", "Pr",
}
_ACCENT_WORDS = {"hat", "bar", "tilde", "vec", "dot", "ddot", "check", "acute",
                 "grave", "arch", "under", "dyad", "overline", "underline"}
_GRIDS = {"matrix", "pmatrix", "bmatrix", "dmatrix", "cases", "pile", "lpile",
          "rpile", "eqalign"}
_ZERO = {"rm", "it", "bold", "left", "right", "LEFT", "RIGHT", "not"}

_TOK_RE = re.compile(
    r'"[^"]*"'
    r"|<->|<=>|<=|>=|!=|\+-|-\+|->|<-|=>"
    r"|[A-Za-z]+"
    r"|[0-9]+(?:\.[0-9]+)?"
    r"|\s+"
    r"|."
)


@dataclass
class Box:
    w: float
    asc: float = _ASC
    desc: float = _DESC

    @property
    def h(self) -> float:
        return self.asc + self.desc


def _tokens(script: str) -> list[str]:
    return [t for t in _TOK_RE.findall(script) if not t.isspace()]


def _char_width(ch: str) -> float:
    if ord(ch) >= 0x1100 and not (0x2000 <= ord(ch) < 0x2E80):
        return _W_WIDE
    if ch.isdigit():
        return _W_DIGIT
    if ch.isupper():
        return _W_UPPER
    if ch.isalpha():
        return _W_LOWER
    if ch in "()[],.;:|!'":
        return _W_PUNCT
    return _W_SYMBOL


class _Layout:
    def __init__(self, toks: list[str]):
        self.toks = toks
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> str | None:
        t = self.peek()
        if t is not None:
            self.i += 1
        return t

    # an expression up to '}' / '#' / '&' / end, as a list of boxes joined in a row
    def row(self, stops=("}",)) -> Box:
        boxes: list[Box] = []
        while (t := self.peek()) is not None and t not in stops:
            if t in ("over", "atop"):
                self.take()
                num = _hjoin(boxes)
                den = self.atom_with_scripts()
                boxes = [_frac(num, den)]
                continue
            boxes.append(self.atom_with_scripts())
        return _hjoin(boxes)

    def group(self) -> Box:
        """``{…}`` (or one atom when no brace follows)."""
        if self.peek() == "{":
            self.take()
            b = self.row(("}",))
            if self.peek() == "}":
                self.take()
            return b
        return self.atom()

    def atom_with_scripts(self) -> Box:
        t = self.peek()
        base = self.atom()
        big = t in _BIG
        integral = t in _INTEGRALS
        sub = sup = None
        while self.peek() in ("_", "^"):
            which = self.take()
            arg = _scale(self.group(), _SCRIPT)
            if which == "_":
                sub = arg
            else:
                sup = arg
        if sub is None and sup is None:
            return base
        if big:  # limits above / below
            w = max(base.w, sub.w if sub else 0, sup.w if sup else 0)
            return Box(w, base.asc + (sup.h + 0.05 if sup else 0),
                       base.desc + (sub.h + 0.05 if sub else 0))
        w = base.w + max(sub.w if sub else 0, sup.w if sup else 0) + 0.05
        asc = max(base.asc, (base.asc * 0.55 + sup.h) if sup else 0)
        desc = max(base.desc, (sub.h - 0.15) if sub else 0)
        if integral:
            w += 0.1
        return Box(w, asc, desc)

    def atom(self) -> Box:
        t = self.take()
        if t is None:
            return Box(0, 0, 0)
        if t == "{":
            b = self.row(("}",))
            if self.peek() == "}":
                self.take()
            return b
        if t in ("}", "#", "&"):
            return Box(0, 0, 0)
        if t in _ZERO:
            if t in ("LEFT", "left"):
                return self.delimited()
            return Box(0, 0, 0)
        if t == "~":
            return Box(0.5, 0, 0)
        if t == "`":
            return Box(0.125, 0, 0)
        if t.startswith('"'):
            return Box(sum(_char_width(c) for c in t[1:-1]) or 0.3)
        if t in ("sqrt",):
            body = self.group()
            return Box(body.w + 0.85, body.asc + 0.18, body.desc)
        if t == "root":
            idx = _scale(self.group(), 0.5)
            if self.peek() == "of":
                self.take()
            body = self.group()
            return Box(body.w + 0.85 + max(0, idx.w - 0.3), body.asc + 0.18, body.desc)
        if t in _ACCENT_WORDS:
            body = self.group()
            return Box(body.w, body.asc + 0.2, body.desc)
        if t in _GRIDS:
            return self.grid(t)
        if t in _BIG:
            return Box(1.25 if t not in ("lim", "Lim", "max", "min") else 0.5 * len(t) + 0.2,
                       1.0 if t not in ("lim", "Lim", "max", "min") else _ASC,
                       0.35 if t not in ("lim", "Lim", "max", "min") else _DESC)
        if t in _INTEGRALS:
            return Box(0.75 * (2 if t == "dint" else 3 if t == "tint" else 1), 1.1, 0.4)
        if t in _BINOPS:
            return Box(_W_BINOP)
        if t in _FUNC_WORDS:
            return Box(0.48 * len(t) + 0.15)
        if t.isalpha() and len(t) > 1 and t.isascii():
            # a command drawing one glyph (greek, symbols) or an identifier run
            from .equation import _KEYWORDS
            if t.lower() in _GREEK:
                return Box(_W_GREEK)
            if t.lower() in _KEYWORDS:
                return Box(_W_SYMBOL)
            return Box(sum(_char_width(c) for c in t))
        return Box(sum(_char_width(c) for c in t))

    def delimited(self) -> Box:
        self.take()  # the opening delimiter
        depth = 1
        start = self.i
        while self.i < len(self.toks):
            t = self.toks[self.i]
            if t in ("LEFT", "left"):
                depth += 1
            elif t in ("RIGHT", "right"):
                depth -= 1
                if depth == 0:
                    break
            self.i += 1
        inner = _Layout(self.toks[start : self.i]).row(())
        self.i = min(self.i + 2, len(self.toks))  # RIGHT + its delimiter
        grow = max(0.0, inner.h - (_ASC + _DESC))
        side = 0.35 + 0.08 * grow
        return Box(inner.w + 2 * side, inner.asc + 0.08, inner.desc + 0.08)

    def grid(self, kind: str) -> Box:
        if self.peek() != "{":
            return Box(0, 0, 0)
        self.take()
        rows: list[list[Box]] = [[]]
        while self.peek() not in (None, "}"):
            rows[-1].append(self.row(("}", "#", "&")))
            sep = self.peek()
            if sep == "&":
                self.take()
            elif sep == "#":
                self.take()
                rows.append([])
        if self.peek() == "}":
            self.take()
        rows = [r for r in rows if r]
        if not rows:
            return Box(0, 0, 0)
        ncol = max(len(r) for r in rows)
        widths = [max((r[c].w for r in rows if c < len(r)), default=0) for c in range(ncol)]
        gap = 0.3 if kind in ("eqalign", "pile", "lpile", "rpile") else 0.9
        w = sum(widths) + gap * (ncol - 1)
        heights = [max((b.h for b in r), default=_ASC + _DESC) + 0.25 for r in rows]
        total = sum(heights)
        if kind in ("pmatrix", "bmatrix", "dmatrix"):
            w += 1.0
        elif kind == "cases":
            w += 0.6
        return Box(w, total / 2 + 0.3, total / 2 - 0.3)


def _hjoin(boxes: list[Box]) -> Box:
    if not boxes:
        return Box(0, 0, 0)
    return Box(sum(b.w for b in boxes), max(b.asc for b in boxes), max(b.desc for b in boxes))


def _frac(num: Box, den: Box) -> Box:
    return Box(max(num.w, den.w) + 0.25, num.h + 0.32, den.h + 0.02)


def _scale(b: Box, k: float) -> Box:
    return Box(b.w * k, b.asc * k, b.desc * k)


def layout(script: str) -> Box:
    """The rendered box of a Hangul equation *script*, in em."""
    toks = _tokens(script)
    # top-level '#' starts a new line (multi-line equations)
    lines: list[list[str]] = [[]]
    depth = 0
    for t in toks:
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
        if t == "#" and depth == 0:
            lines.append([])
        else:
            lines[-1].append(t)
    boxes = [_Layout(ln).row(()) for ln in lines if ln]
    if not boxes:
        return Box(0.5)
    if len(boxes) == 1:
        return boxes[0]
    w = max(b.w for b in boxes)
    total = sum(b.h + 0.2 for b in boxes)
    first = boxes[0]
    return Box(w, first.asc, total - first.asc)
