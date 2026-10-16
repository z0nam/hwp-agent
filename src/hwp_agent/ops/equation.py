"""LaTeX math → Hangul equation-editor script (issue #5).

Hangul's equation object (``<hp:equation>``) does not take LaTeX; it stores its own
script language (``{a} over {b}``, ``sum _{t=1} ^{n}``, ``LEFT ( … RIGHT )``).
:func:`latex_to_hwp` converts the LaTeX subset that shows up in research papers
and reports, and :func:`build_equation_run` wraps a script in the ``<hp:run>`` /
``<hp:equation>`` markup Hangul itself writes (copied from a Hangul-saved file).

Two traps the converter handles explicitly (both from a real journal port):

* ``rm`` / ``it`` are *mode toggles*, not group commands — ``rm s.t. ~ y _t`` sets
  ``y`` upright too. Every upright span (``\\text``, ``\\mathrm``,
  ``\\operatorname`` …) is therefore emitted as ``rm … it`` so the mode is closed.
  Not as ``{rm … it}``: Hangul 2022 **crashes** converting a brace group that holds
  ``rm`` when a ``_``/``^`` follows it (``{rm argmin} _{beta}``), while
  ``rm argmin it _{beta}`` sets the subscript on ``argmin`` fine (hwp2pdf, 2026-10-16).
  Quoting is no substitute either — ``"argmin"`` renders italic.
* Some LaTeX has no Hangul counterpart (``\\overset``, ``\\underbrace`` …). Those
  are approximated (``\\overset{a}{b}`` → ``b ^{a}``) and reported as warnings
  rather than silently dropped.

Words that collide with script keywords (``over``, ``it``, ``inf`` …) are quoted,
the way Hangul users escape them (``"it"``); text is written with the script's own
spacing (`` ` `` = ¼ space, ``~`` = space).

A raw escape hatch: ``\\hwp{…}`` passes Hangul script through untouched. ``extract``
writes existing equations that way, so a document round-trips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .eqlayout import layout

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

# ---------------------------------------------------------------------------
# mapping tables

#: LaTeX command → script token, for plain symbols (no arguments)
_SYMBOLS: dict[str, str] = {
    # Greek — lower case as-is; upper case is Hangul's capitalised name
    **{g: g for g in (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
        "omicron pi rho sigma tau upsilon phi chi psi omega"
    ).split()},
    "varepsilon": "varepsilon", "vartheta": "vartheta", "varphi": "varphi",
    "varpi": "varpi", "varrho": "varrho", "varsigma": "varsigma",
    **{g: g for g in "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split()},
    # binary operators
    "times": "times", "cdot": "cdot", "div": "div", "pm": "+-", "mp": "-+",
    "ast": "*", "star": "star", "circ": "circ", "bullet": "bullet",
    "oplus": "oplus", "ominus": "ominus", "otimes": "otimes", "odot": "odot",
    "cup": "cup", "cap": "cap", "setminus": "\\", "wedge": "wedge", "vee": "vee",
    "land": "wedge", "lor": "vee",
    # relations
    "le": "<=", "leq": "<=", "ge": ">=", "geq": ">=", "neq": "!=", "ne": "!=",
    "lt": "<", "gt": ">", "leqslant": "<=", "geqslant": ">=",
    "ll": "<<", "gg": ">>",
    "approx": "approx", "sim": "sim", "simeq": "simeq", "cong": "cong",
    "equiv": "equiv", "propto": "propto", "doteq": "doteq",
    "in": "in", "notin": "notin", "ni": "owns", "subset": "subset",
    "supset": "supset", "subseteq": "subseteq", "supseteq": "supseteq",
    "perp": "bot", "parallel": "∥", "mid": "|",
    "top": 'rm "T" it', "intercal": 'rm "T" it',
    # arrows
    "to": "->", "rightarrow": "->", "leftarrow": "<-", "gets": "<-",
    # (Hangul has no `=>` — it prints the two characters; RARROW/LARROW are ⇒/⇐)
    "leftrightarrow": "<->", "Rightarrow": "RARROW", "Leftarrow": "LARROW",
    "Leftrightarrow": "LRARROW", "implies": "RARROW", "iff": "LRARROW", "mapsto": "↦",
    "uparrow": "uparrow", "downarrow": "downarrow",
    "longrightarrow": "->", "Longrightarrow": "RARROW", "longleftarrow": "<-",
    # misc symbols
    # (`forall` is not a Hangul command — it prints "for all")
    "infty": "inf", "partial": "partial", "nabla": "nabla", "forall": "∀",
    "exists": "exist", "emptyset": "emptyset", "varnothing": "emptyset",
    "neg": "neg", "lnot": "neg", "therefore": "therefore", "because": "because",
    "angle": "angle", "triangle": "triangle", "prime": "prime", "ell": "ell",
    "hbar": "hbar", "Re": "Re", "Im": "Im", "aleph": "aleph",
    "cdots": "cdots", "ldots": "cdots", "dots": "cdots", "dotsc": "cdots",
    "dotsb": "cdots", "vdots": "vdots", "ddots": "ddots",
    "lbrace": "lbrace", "rbrace": "rbrace", "langle": "langle", "rangle": "rangle",
    "lfloor": "lfloor", "rfloor": "rfloor", "lceil": "lceil", "rceil": "rceil",
    "vert": "|", "lvert": "|", "rvert": "|",
    "Vert": "∥", "lVert": "∥", "rVert": "∥",
    "%": "%", "$": "$", "&": "&", "_": "_", "#": '"#"',
    "{": "lbrace", "}": "rbrace", "|": "∥",
    # spacing
    ",": "`", ":": "~", ";": "~", " ": "~", "quad": "~~", "qquad": "~~~~",
    "!": "", "enspace": "~", "thinspace": "`",
}

#: big operators (limits attach with _ / ^ as usual)
_BIG_OPS: dict[str, str] = {
    "sum": "sum", "prod": "prod", "coprod": "coprod", "int": "int",
    "iint": "dint", "iiint": "tint", "oint": "oint",
    "bigcup": "union", "bigcap": "inter", "bigoplus": "oplus", "bigotimes": "otimes",
}

#: function names Hangul sets upright by itself
_FUNCS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "coth",
    "arcsin", "arccos", "arctan", "exp", "log", "ln", "lg", "lim", "max", "min",
    "sup", "det", "gcd", "deg", "dim", "ker", "hom", "arg", "mod", "Pr",
}
#: LaTeX operator names Hangul has no function for — written as upright text
_TEXT_FUNCS = {"inf", "liminf", "limsup", "argmax", "argmin", "sgn", "var", "cov", "tr"}

#: one-argument accents
_ACCENTS = {
    "hat": "hat", "widehat": "hat", "bar": "bar", "overline": "bar",
    "tilde": "tilde", "widetilde": "tilde", "vec": "vec", "overrightarrow": "vec",
    "dot": "dot", "ddot": "ddot", "check": "check", "acute": "acute",
    "grave": "grave", "breve": "arch", "underline": "under",
}

#: upright-text commands (closed with ``it`` — see module docstring)
_TEXT_CMDS = {"text", "textrm", "mathrm", "textup", "mbox", "operatorname", "textnormal", "mathup"}

#: commands whose argument is simply kept (styling Hangul can't express) — no warning
_PASS_CMDS = {"displaystyle", "textstyle", "scriptstyle", "limits", "nolimits",
              "left.", "right.", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl",
              "Bigr", "biggl", "biggr", "Biggl", "Biggr", "nonumber", "notag"}

_BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "E": "𝔼"}

#: matrix-like environments → Hangul container
_ENVS = {
    "matrix": "matrix", "pmatrix": "pmatrix", "bmatrix": "bmatrix",
    "vmatrix": "dmatrix", "Vmatrix": "dmatrix", "smallmatrix": "matrix",
    "array": "matrix", "cases": "cases",
    "aligned": "eqalign", "align": "eqalign", "align*": "eqalign",
    "alignat": "eqalign", "alignedat": "eqalign", "split": "eqalign",
    "eqnarray": "eqalign", "eqnarray*": "eqalign",
    "gather": "pile", "gather*": "pile", "gathered": "pile",
    "multline": "pile", "multline*": "pile", "equation": "", "equation*": "",
}

#: left/right delimiters
_DELIMS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "|": "|", ".": "",
    "\\{": "lbrace", "\\}": "rbrace", "\\|": "∥", "\\lVert": "∥", "\\rVert": "∥",
    "\\Vert": "∥", "\\vert": "|", "\\lvert": "|", "\\rvert": "|",
    "\\langle": "langle", "\\rangle": "rangle", "\\lfloor": "lfloor",
    "\\rfloor": "rfloor", "\\lceil": "lceil", "\\rceil": "rceil",
    "\\lbrace": "lbrace", "\\rbrace": "rbrace", "<": "langle", ">": "rangle",
}

#: words the script reads as commands — a LaTeX identifier spelled like one is quoted
_KEYWORDS = {k.lower() for k in (
    set(_SYMBOLS.values()) | set(_BIG_OPS.values()) | set(_ACCENTS.values())
    | set(_ENVS.values()) | _FUNCS
)} | {
    "over", "atop", "sqrt", "root", "of", "left", "right", "rm", "it", "bold",
    "sub", "sup", "from", "to", "pile", "lpile", "rpile", "rel", "buildrel",
    "under", "not", "box", "size", "color", "inf", "matrix", "cases", "eqalign",
    "lim", "union", "inter", "smallsum", "smallprod", "smallint", "times",
    "and", "or", "if", "for", "le", "ge", "ne", "in", "bot", "top",
}
_KEYWORDS.discard("")


# ---------------------------------------------------------------------------
# tokenizer

_TOKEN_RE = re.compile(
    r"\\([A-Za-z]+\*?)"      # \command (optionally starred)
    r"|\\(.)"                # \, \{ \| \\ …
    r"|(\s+)"
    r"|([A-Za-z]+)"          # a run of letters
    r"|([0-9]+(?:\.[0-9]+)?)"
    r"|(.)",
    re.S,
)


@dataclass
class _Tok:
    kind: str  # cmd | sym | ws | word | num | ch
    val: str


def _tokenize(src: str) -> list[_Tok]:
    out: list[_Tok] = []
    for m in _TOKEN_RE.finditer(src):
        if m.group(1) is not None:
            out.append(_Tok("cmd", m.group(1)))
        elif m.group(2) is not None:
            out.append(_Tok("sym", m.group(2)))
        elif m.group(3) is not None:
            out.append(_Tok("ws", m.group(3)))
        elif m.group(4) is not None:
            out.append(_Tok("word", m.group(4)))
        elif m.group(5) is not None:
            out.append(_Tok("num", m.group(5)))
        else:
            out.append(_Tok("ch", m.group(6)))
    return out


# ---------------------------------------------------------------------------
# converter


@dataclass
class EquationResult:
    script: str
    warnings: list[str] = field(default_factory=list)


def _quote_text(text: str) -> str:
    """Literal text for an ``rm … it`` span: every Latin word quoted, spaces as ``~``.

    Unquoted, Hangul splits a word into known names — ``rm argmin it`` came out as
    "arg" + garbage glyphs (hwp2pdf, 2026-10-16); ``rm "argmin" it`` is upright and
    intact. Quoting alone is not enough: a quoted word is still italic."""
    parts = []
    for word in re.split(r"(\s+)", text):
        if not word:
            continue
        if word.isspace():
            parts.append("~")
        elif re.search(r'[A-Za-z{}^_~`#&\\]', word):
            parts.append('"' + word.replace('"', "") + '"')
        else:
            parts.append(word)
    return " ".join(parts)


class _Parser:
    def __init__(self, src: str):
        self.toks = _tokenize(src)
        self.i = 0
        self.warnings: list[str] = []

    # -- helpers -----------------------------------------------------------
    def peek(self, skip_ws: bool = True) -> _Tok | None:
        j = self.i
        while skip_ws and j < len(self.toks) and self.toks[j].kind == "ws":
            j += 1
        return self.toks[j] if j < len(self.toks) else None

    def next(self, skip_ws: bool = True) -> _Tok | None:
        while skip_ws and self.i < len(self.toks) and self.toks[self.i].kind == "ws":
            self.i += 1
        if self.i >= len(self.toks):
            return None
        t = self.toks[self.i]
        self.i += 1
        return t

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def raw_group(self) -> str:
        """The verbatim source text of the next ``{…}`` group (or single token)."""
        t = self.next()
        if t is None:
            return ""
        if not (t.kind == "ch" and t.val == "{"):
            return _tok_src(t)
        depth, buf = 1, []
        while self.i < len(self.toks):
            t = self.toks[self.i]
            self.i += 1
            if t.kind == "ch" and t.val == "{":
                depth += 1
            elif t.kind == "ch" and t.val == "}":
                depth -= 1
                if depth == 0:
                    break
            buf.append(_tok_src(t))
        return "".join(buf)

    def optional_arg(self) -> str | None:
        t = self.peek()
        if t is not None and t.kind == "ch" and t.val == "[":
            self.next()
            depth, buf = 1, []
            while self.i < len(self.toks):
                t = self.toks[self.i]
                self.i += 1
                if t.kind == "ch" and t.val == "[":
                    depth += 1
                elif t.kind == "ch" and t.val == "]":
                    depth -= 1
                    if depth == 0:
                        break
                buf.append(_tok_src(t))
            return "".join(buf)
        return None

    def sub(self, src: str) -> str:
        p = _Parser(src)
        out = p.expr()
        for w in p.warnings:
            self.warn(w)
        return out

    def arg(self) -> str:
        """The next argument converted, wrapped in ``{}``."""
        return "{" + self.sub(self.raw_group()) + "}"

    # -- grammar -----------------------------------------------------------
    def expr(self) -> str:
        parts: list[str] = []
        while self.peek() is not None:
            piece = self.atom()
            if piece:
                parts.append(piece)
        return _join(parts)

    def atom(self) -> str:
        t = self.next()
        if t is None:
            return ""
        k, v = t.kind, t.val
        if k == "ch":
            if v == "{":
                self.i -= 1
                return "{" + self.sub(self.raw_group()) + "}"
            if v == "}":
                self.warn("unbalanced '}' dropped")
                return ""
            if v in "^_":
                return f"{v}{self.script_arg()}"
            if v == "'":
                return "prime"
            if v == "~":
                return "~"
            if v == "&":
                return "&"
            if v == "#":
                return '"#"'
            if v == '"':
                return ""
            if v == "`":
                return ""
            return v
        if k == "num":
            return v
        if k == "word":
            return _ident(v)
        if k == "sym":
            if v == "\\":
                return "#"
            if v in _SYMBOLS:
                return _SYMBOLS[v]
            return v
        return self.command(v)

    def script_arg(self) -> str:
        """Argument of ``^`` / ``_``: a group, a single char/digit, or a command."""
        t = self.peek()
        if t is None:
            return "{}"
        if t.kind == "ch" and t.val == "{":
            return "{" + self.sub(self.raw_group()) + "}"
        self.next()
        if t.kind == "word":  # x_ij → only the first letter is the subscript
            rest = t.val[1:]
            if rest:
                self.toks.insert(self.i, _Tok("word", rest))
            return "{" + _ident(t.val[0]) + "}"
        if t.kind == "num":
            rest = t.val[1:]
            if rest:
                self.toks.insert(self.i, _Tok("num", rest))
            return "{" + t.val[0] + "}"
        if t.kind == "cmd":
            return "{" + self.command(t.val) + "}"
        if t.kind == "sym":
            return "{" + _SYMBOLS.get(t.val, t.val) + "}"
        if t.val == "'":
            return "{prime}"
        return "{" + t.val + "}"

    def command(self, name: str) -> str:
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, den = self.arg(), self.arg()
            return f"{num} over {den}"
        if name in ("binom", "dbinom", "tbinom"):
            n, k = self.arg(), self.arg()
            return f"LEFT ( {n} atop {k} RIGHT )"
        if name == "sqrt":
            idx = self.optional_arg()
            body = self.arg()
            if idx is not None:
                return f"root {{{self.sub(idx)}}} of {body}"
            return f"sqrt {body}"
        if name == "left":
            return self.left_right()
        if name == "right":
            self.warn("\\right without \\left — kept as a plain delimiter")
            return _DELIMS.get(self.delim(), "")
        if name in _ACCENTS:
            # grouped: a following _/^ belongs outside the accent — `hat {y} _{t}`
            # stretches the hat over the subscript too (Hangul render, 2026-10-16)
            return f"{{{_ACCENTS[name]} {self.arg()}}}"
        if name in _TEXT_CMDS or name == "operatorname*":
            text = self.raw_group()
            if name in ("operatorname", "operatorname*", "mathrm", "mathup"):
                # math-mode content: \, etc. are spacing, not text
                text = re.sub(r"\\[,;:! ]", "", text).replace("\\", "")
            return f"rm {_quote_text(text)} it"
        if name in ("mathbf", "boldsymbol", "bm", "textbf", "mathbfit"):
            return f"bold {self.arg()}"
        if name in ("mathit", "textit", "mathnormal", "emph"):
            return f"it {self.arg()}"
        if name == "mathbb":
            inner = self.raw_group().strip()
            if inner in _BLACKBOARD:
                return _BLACKBOARD[inner]
            self.warn(f"\\mathbb{{{inner}}} has no Hangul glyph — written as bold")
            return f"bold {{{self.sub(inner)}}}"
        if name in ("mathcal", "mathscr", "mathfrak", "mathsf", "mathtt", "texttt"):
            inner = self.raw_group()
            self.warn(f"\\{name} has no Hangul font — written as plain letters")
            return "{" + self.sub(inner) + "}"
        if name in ("overset", "stackrel"):
            top, base = self.arg(), self.arg()
            self.warn(f"\\{name} has no Hangul equivalent — approximated as a superscript")
            return f"{base} ^{top}"
        if name == "underset":
            bot, base = self.arg(), self.arg()
            self.warn("\\underset has no Hangul equivalent — approximated as a subscript")
            return f"{base} _{bot}"
        if name in ("overbrace", "underbrace"):
            body = self.arg()
            self.warn(f"\\{name} has no Hangul equivalent — brace dropped")
            return body
        if name == "not":
            nxt = self.atom()
            return f"not {nxt}"
        if name in ("begin",):
            return self.environment()
        if name == "end":
            self.raw_group()
            self.warn("\\end without \\begin dropped")
            return ""
        if name in ("label", "nonumber", "notag"):
            if name == "label":
                self.raw_group()
            return ""
        if name in ("tag", "tag*"):
            tag = self.raw_group()
            return f'~~~~ ({_quote_text(tag)})'
        if name in ("color", "textcolor"):
            self.raw_group()
            self.warn(f"\\{name} ignored (equation colour is set on the object)")
            return ""
        if name in ("hspace", "vspace", "phantom", "hphantom", "vphantom"):
            self.raw_group()
            return "~" if name != "vspace" else ""
        if name in _PASS_CMDS:
            return ""
        if name in _BIG_OPS:
            return _BIG_OPS[name]
        if name in _FUNCS:
            return name
        if name in _TEXT_FUNCS:
            return f'rm "{name}" it'
        if name in _SYMBOLS:
            return _SYMBOLS[name]
        if name == "hwp":
            return self.raw_group()
        self.warn(f"unknown command \\{name} — written as its name")
        return f'rm "{name}" it'

    def delim(self) -> str:
        t = self.next()
        if t is None:
            return "."
        if t.kind == "cmd":
            return "\\" + t.val
        if t.kind == "sym":
            return "\\" + t.val
        return t.val

    def left_right(self) -> str:
        open_d = self.delim()
        depth, start = 1, self.i
        j = self.i
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == "cmd" and t.val == "left":
                depth += 1
            elif t.kind == "cmd" and t.val == "right":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner_src = "".join(_tok_src(t) for t in self.toks[start:j])
        self.i = j + 1 if j < len(self.toks) else j
        close_d = self.delim() if j < len(self.toks) else "."
        if j >= len(self.toks):
            self.warn("\\left without \\right — closed at the end")
        inner = self.sub(inner_src)
        lo, ro = _DELIMS.get(open_d), _DELIMS.get(close_d)
        if lo is None or ro is None:
            self.warn(f"unsupported delimiter in \\left{open_d} … \\right{close_d}")
            lo, ro = lo or "", ro or ""
        if not lo and not ro:
            return "{" + inner + "}"
        # Hangul wants both sides; an empty side ('.') becomes a group only
        if not lo:
            return f"{{{inner}}} {ro}"
        if not ro:
            return f"{lo} {{{inner}}}"
        return f"LEFT {lo} {inner} RIGHT {ro}"

    def environment(self) -> str:
        env = self.raw_group().strip()
        # body = everything up to the matching \end{env}
        depth, start, j = 1, self.i, self.i
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == "cmd" and t.val in ("begin", "end"):
                # look at the env name following
                k = j + 1
                name = ""
                if k < len(self.toks) and self.toks[k].val == "{":
                    k += 1
                    while k < len(self.toks) and self.toks[k].val != "}":
                        name += _tok_src(self.toks[k])
                        k += 1
                if name.strip() == env:
                    depth += 1 if t.val == "begin" else -1
                    if depth == 0:
                        break
            j += 1
        body_toks = self.toks[start:j]
        # skip "\end{env}"
        self.i = j
        if self.i < len(self.toks):
            self.next()
            self.raw_group()
        else:
            self.warn(f"\\begin{{{env}}} without \\end — closed at the end")
        if env in ("array", "alignat", "alignedat"):
            # drop the column spec / column count argument
            while body_toks and body_toks[0].kind == "ws":
                body_toks = body_toks[1:]
            if body_toks and body_toks[0].val == "{":
                d = 0
                for n, t in enumerate(body_toks):
                    if t.val == "{":
                        d += 1
                    elif t.val == "}":
                        d -= 1
                        if d == 0:
                            body_toks = body_toks[n + 1 :]
                            break
        body_src = "".join(_tok_src(t) for t in body_toks)
        target = _ENVS.get(env)
        if target is None:
            self.warn(f"unknown environment {env} — contents kept")
            return self.sub(body_src)
        rows = _split_rows(body_src)
        if target == "":  # equation: just the body
            return " ".join(self.sub(r) for r in rows)
        if target == "pile":
            return "pile{" + " # ".join(self.sub(r.replace("&", "")) for r in rows) + "}"
        conv = [" & ".join(self.sub(c) for c in _split_cells(r)) for r in rows]
        return f"{target}{{" + " # ".join(conv) + "}"


def _split_rows(src: str) -> list[str]:
    """Split an environment body at top-level ``\\\\`` (dropping an empty last row).

    Top level means outside braces *and* outside nested ``\\begin…\\end`` pairs, so a
    ``cases`` inside ``aligned`` keeps its own rows."""
    rows, depth, buf, i = [], 0, [], 0
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif src.startswith("\\\\", i) and depth != 0:
            buf.append("\\\\")  # nested row break: keep both chars together
            i += 2
            continue
        elif _BEGIN_RE.match(src, i):
            depth += 1
        elif _END_RE.match(src, i):
            depth -= 1
        if depth == 0 and src.startswith("\\\\", i):
            rows.append("".join(buf))
            buf = []
            i += 2
            # optional [2pt] spacing after \\
            m = re.match(r"\s*\[[^\]]*\]", src[i:])
            if m:
                i += m.end()
            continue
        if depth == 0 and src.startswith(r"\hline", i):
            i += len(r"\hline")
            continue
        buf.append(c)
        i += 1
    rows.append("".join(buf))
    if rows and not rows[-1].strip():
        rows.pop()
    return rows or [""]


_BEGIN_RE = re.compile(r"\\begin(?![A-Za-z])")
_END_RE = re.compile(r"\\end(?![A-Za-z])")


def _split_cells(row: str) -> list[str]:
    cells, depth, buf = [], 0, []
    for i, c in enumerate(row):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif _BEGIN_RE.match(row, i) and (i == 0 or row[i - 1] != "\\"):
            depth += 1
        elif _END_RE.match(row, i) and (i == 0 or row[i - 1] != "\\"):
            depth -= 1
        if c == "&" and depth == 0 and (i == 0 or row[i - 1] != "\\"):
            cells.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    cells.append("".join(buf))
    return cells


def _tok_src(t: _Tok) -> str:
    if t.kind == "cmd":
        return "\\" + t.val
    if t.kind == "sym":
        return "\\" + t.val
    return t.val


def _ident(word: str) -> str:
    """A LaTeX identifier run (letters in math = product of variables)."""
    if word.lower() in _KEYWORDS:
        return '"' + word + '"'
    return word


def _join(parts: list[str]) -> str:
    return re.sub(r"[ ]{2,}", " ", " ".join(parts)).strip()


_STYLE_WORD_RE = re.compile(r"(?<![\w\"])(rm|it|bold)(?![\w\"])", re.I)


def _defuse_styled_groups(script: str) -> tuple[str, list[str]]:
    """Guard against the Hangul crash on ``{… rm …} _{x}`` (see module docstring).

    A group that is exactly one upright span (``{rm d it}``) loses its braces — same
    rendering, no crash. Any other group holding a style toggle right before
    ``_``/``^`` is left alone and reported."""
    warnings: list[str] = []
    out = script
    i = 0
    while True:
        i = out.find("{", i)
        if i < 0:
            break
        depth, j = 0, i
        while j < len(out):
            if out[j] == "{":
                depth += 1
            elif out[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(out):
            break
        inner = out[i + 1 : j]
        follows = out[j + 1 :].lstrip()[:1]
        if follows in ("_", "^") and _STYLE_WORD_RE.search(re.sub(r'"[^"]*"', "", inner)):
            if re.fullmatch(r"\s*rm\s+[^{}]*?\s+it\s*", inner):
                out = out[:i] + inner.strip() + out[j + 1 :]
                continue
            warnings.append(
                f"group {{{inner.strip()}}} sets rm/it/bold and is followed by a "
                "sub/superscript — Hangul may crash on it; move the style inside"
            )
        i += 1
    return out, warnings


def latex_to_hwp(latex: str) -> EquationResult:
    """Convert a LaTeX math string (without ``$`` delimiters) to Hangul script."""
    p = _Parser(latex.strip())
    script, warns = _defuse_styled_groups(p.expr())
    return EquationResult(script=script, warnings=p.warnings + warns)


# ---------------------------------------------------------------------------
# Markdown math spans

#: ``$$…$$`` (display) or ``$…$`` (inline, pandoc rules: no space just inside the
#: dollars, closing ``$`` not followed by a digit — so "$5 and $10" is not math).
#: The opening ``$`` may follow a letter: Korean prose glues particles to math
#: (``식$x^2$은``), and extract emits markers that way.
_MATH_RE = re.compile(
    r"(?<![\\$])\$\$(?P<d>.+?)\$\$"
    r"|(?<![\\$])\$(?P<i>[^\s$](?:[^$]*?[^\s\\$])?)\$(?![\d$])",
    re.S,
)


def math_pieces(text: str) -> list[tuple[str, str | None]]:
    """Split *text* into ``(text, None)`` and ``(latex, kind)`` pieces where kind is
    ``"inline"`` or ``"display"``. ``\\$`` stays a literal dollar."""
    pieces: list[tuple[str, str | None]] = []
    pos = 0
    for m in _MATH_RE.finditer(text):
        if m.start() > pos:
            pieces.append((text[pos : m.start()], None))
        if m.group("d") is not None:
            pieces.append((m.group("d"), "display"))
        else:
            pieces.append((m.group("i"), "inline"))
        pos = m.end()
    if pos < len(text):
        pieces.append((text[pos:], None))
    return pieces or [(text, None)]


def unescape_dollars(text: str) -> str:
    return text.replace("\\$", "$")


# ---------------------------------------------------------------------------
# hp:equation markup


#: the estimated width is padded: Hangul draws the formula at its true size inside
#: the stored box, so a box that is too narrow overprints the next text while one a
#: little too wide only leaves a small gap
_WIDTH_PAD = 1.08
_WIDTH_MARGIN = 0.2  # em — Hangul's own side bearing, visible on 1-letter formulas


def equation_size(script: str, base_unit: int) -> tuple[int, int, int]:
    """Estimated ``(width, height, baseLine%)`` for *script* at *base_unit*.

    Hangul keeps the stored box until the equation is edited (see
    :mod:`hwp_agent.ops.eqlayout`), so this decides the room the line reserves."""
    box = layout(script)
    w = int((box.w * _WIDTH_PAD + _WIDTH_MARGIN) * base_unit)
    h = max(int(box.h * base_unit), base_unit)
    base_line = round(box.asc / box.h * 100) + 7 if box.h else 86
    return w, h, max(10, min(95, base_line))


def equation_xml(
    script: str, eq_id: int, *, base_unit: int = 1000, z_order: int = 0
) -> str:
    """``<hp:equation>`` markup as Hangul writes it (inline, treat-as-char)."""
    w, h, base_line = equation_size(script, base_unit)
    esc = script.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<hp:equation id="{eq_id}" zOrder="{z_order}" numberingType="EQUATION" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'version="Equation Version 60" baseLine="{base_line}" textColor="#000000" '
        f'baseUnit="{base_unit}" lineMode="CHAR" font="HancomEQN">'
        f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
        f"<hp:shapeComment>수식입니다.</hp:shapeComment>"
        f"<hp:script>{esc}</hp:script></hp:equation>"
    )


def equation_run_xml(
    script: str, eq_id: int, char_pr=None, *, base_unit: int = 1000, z_order: int = 0
) -> str:
    """A ``<hp:run>`` holding one equation (plus the empty ``<hp:t/>`` Hangul adds)."""
    c = f' charPrIDRef="{char_pr}"' if char_pr is not None else ""
    return (
        f'<hp:run xmlns:hp="{_HP}"{c}>'
        + equation_xml(script, eq_id, base_unit=base_unit, z_order=z_order)
        + "<hp:t/></hp:run>"
    )
