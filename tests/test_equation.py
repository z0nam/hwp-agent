"""LaTeX math ↔ Hangul equation objects (issue #5)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops import fill_from_markdown
from hwp_agent.ops.author import _split_table_row, inline_segments, parse_markdown
from hwp_agent.ops.doctor import diagnose_template
from hwp_agent.ops.equation import equation_run_xml, latex_to_hwp, math_pieces
from hwp_agent.ops.extract import extract_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
APPENDIX = REPO_ROOT / "tests" / "fixtures" / "sample_appendix.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def conv(latex: str) -> str:
    return latex_to_hwp(latex).script


# --------------------------------------------------------------------------- #
# converter
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "latex, script",
    [
        (r"\frac{a}{b}", "{a} over {b}"),
        (r"x_{i}^{2}", "x _{i} ^{2}"),
        (r"x_ij", "x _{i} j"),  # only the first letter is the subscript, as in TeX
        (r"\sum_{t=1}^{n} x_t", "sum _{t = 1} ^{n} x _{t}"),
        (r"\sqrt{x} + \sqrt[3]{y}", "sqrt {x} + root {3} of {y}"),
        (r"\lVert w\rVert^2", "∥ w ∥ ^{2}"),
        (r"\|\beta\|_1", "∥ beta ∥ _{1}"),
        (r"\varepsilon,\varphi,\Phi,\Theta,\lambda", "varepsilon , varphi , Phi , Theta , lambda"),
        (r"a \cdots b \sim c \times d \le e \ge f", "a cdots b sim c times d <= e >= f"),
        (r"\exp(x)", "exp ( x )"),
        (r"a\,b\quad c", "a ` b ~~ c"),
        (r"\left( \frac{a}{b} \right)", "LEFT ( {a} over {b} RIGHT )"),
        (r"\left\{ x \right\}", "LEFT lbrace x RIGHT rbrace"),
        (r"\left| x \right|", "LEFT | x RIGHT |"),
        (r"\hat{\beta} + \bar x", "{hat {beta}} + {bar {x}}"),
        # accent grouped so the subscript isn't under the hat (Hangul render)
        (r"\hat{y}_{T+h}", "{hat {y}} _{T + h}"),
        # Hangul has no `=>` / `forall` commands (printed literally)
        (r"p \Rightarrow q \iff r, \forall x", "p RARROW q LRARROW r , ∀ x"),
        (r"f'(x)", "f prime ( x )"),
        (r"\lim_{n\to\infty} a_n", "lim _{n -> inf} a _{n}"),
        (r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}", "pmatrix{a & b # c & d}"),
        (r"\begin{aligned} a &= b \\ &= c \end{aligned}", "eqalign{a & = b # & = c}"),
        (r"\binom{n}{k}", "LEFT ( {n} atop {k} RIGHT )"),
        (r"\mathbb{R}^n", "ℝ ^{n}"),
        (r"\hwp{bar x +- 1.96}", "bar x +- 1.96"),  # raw Hangul script passes through
    ],
)
def test_latex_to_hwp(latex: str, script: str) -> None:
    res = latex_to_hwp(latex)
    assert res.script == script
    assert res.warnings == []


def test_upright_text_closes_rm_mode() -> None:
    # rm is a mode toggle: an unclosed `rm s.t.` would set y_t upright too
    s = conv(r"\text{s.t.}\quad y_t \le \varepsilon")
    assert s.startswith('rm "s.t." it ~~ y _{t}')
    assert s.count("rm ") == s.count(" it")
    # operator names, \mathrm, unknown words: always closed; a sub/superscript
    # after `it` lands on the upright word. Words are quoted — unquoted, Hangul
    # split `argmin` into arg + garbage (Hangul render, 2026-10-16)
    assert conv(r"\operatorname*{arg\,min}_{\beta} f") == 'rm "argmin" it _{beta} f'
    assert conv(r"\mathrm{d}x") == 'rm "d" it x'
    assert conv(r"\inf_x f") == 'rm "inf" it _{x} f'


def test_styled_group_before_script_is_defused() -> None:
    # Hangul 2022 crashes on `{rm d} _{x}` / `{rm d it} _{x}`: a lone upright span
    # loses its braces, anything else is reported
    res = latex_to_hwp(r"{\mathrm{d}}_x + \hwp{{rm a it} ^{2}}")
    assert res.script == 'rm "d" it _{x} + rm a it ^{2}'
    assert res.warnings == []
    res = latex_to_hwp(r"\hwp{{x + rm d} _{i}}")
    assert any("may crash" in w for w in res.warnings)
    # braces inside a superscript are fine (no script follows them)
    assert conv(r"x^\top") == 'x ^{rm "T" it}'


def test_words_are_quoted() -> None:
    # upright words are always quoted; bare identifiers only when they'd read as commands
    assert conv(r"\text{if } x") == 'rm "if" ~ it x'
    assert conv(r"over + it") == '"over" + "it"'
    assert conv(r"\text{the sum} ab") == 'rm "the" ~ "sum" it ab'


def test_cases_environment() -> None:
    s = conv(r"f(x)=\begin{cases} x & \text{if } x>0 \\ 0 & \text{otherwise}\end{cases}")
    assert s == 'f ( x ) = cases{x & rm "if" ~ it x > 0 # 0 & rm "otherwise" it}'


@pytest.mark.parametrize(
    "latex, warn",
    [
        (r"x \overset{iid}{\sim} N", "overset"),
        (r"\underset{x}{\max}", "underset"),
        (r"\underbrace{a+b}", "underbrace"),
        (r"\mathcal{L}", "mathcal"),
        (r"\foo x", "unknown command"),
    ],
)
def test_unsupported_constructs_approximate_with_warning(latex: str, warn: str) -> None:
    res = latex_to_hwp(latex)
    assert res.script  # approximated, not dropped
    assert any(warn in w for w in res.warnings)


def test_overset_approximated_as_superscript() -> None:
    assert conv(r"\varepsilon_t \overset{iid}{\sim} N") == "varepsilon _{t} {sim} ^{iid} N"


# --------------------------------------------------------------------------- #
# Markdown math spans
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("식 $x^2$ 끝", [("식 ", None), ("x^2", "inline"), (" 끝", None)]),
        ("$$a$$ 와 $b$", [("a", "display"), (" 와 ", None), ("b", "inline")]),
        # prices are not math (pandoc rules)
        ("$5 and $10", [("$5 and $10", None)]),
        ("비용 $ 100 $ 원", [("비용 $ 100 $ 원", None)]),
        (r"\$x\$", [(r"\$x\$", None)]),
    ],
)
def test_math_pieces(text, expected) -> None:
    assert math_pieces(text) == expected


def test_emphasis_does_not_eat_math_stars() -> None:
    segs = inline_segments(r"곱 $a*b*c$ 와 **굵게 $x$**")
    assert segs[0].text == "곱 $a*b*c$ 와 "
    assert segs[1].text == "굵게 $x$" and segs[1].bold


def test_table_pipe_inside_math_is_not_a_column() -> None:
    assert _split_table_row(r"| MAPE | $\left|\frac{e}{y}\right|$ |") == [
        "MAPE", r"$\left|\frac{e}{y}\right|$",
    ]


def test_display_math_blocks() -> None:
    blocks = parse_markdown("앞\n\n$$\n\\frac{a}{b}\n$$\n\n$$x^2$$\n\n$$a$$ 와 글\n")
    kinds = [(b.kind, b.text) for b in blocks]
    assert kinds == [
        ("paragraph", "앞"), ("math", "\\frac{a}{b}"), ("math", "x^2"),
        ("paragraph", "$$a$$ 와 글"),  # not standalone → inline in a paragraph
    ]
    assert [b.kind for b in parse_markdown("$$x$$\n", math=False)] == ["paragraph"]


def test_layout_matches_hangul_samples() -> None:
    """Box estimates vs sizes Hangul stored for its own equations (sample_appendix)."""
    from hwp_agent.ops.equation import equation_size

    samples = [  # (script, baseUnit, Hangul's width, height)
        ("bar x", 1000, 705, 1170),
        ("bar x +- 1.96 times s / sqrt N = 68.0 +- 2.7", 800, 9264, 990),
    ]
    for script, bu, w, h in samples:
        ew, eh, _ = equation_size(script, bu)
        assert 0.9 * w <= ew <= 1.5 * w, (script, ew, w)
        assert 0.8 * h <= eh <= 1.4 * h, (script, eh, h)
    # stacking: a fraction is about as wide as its wider part, and taller
    fw, fh, fb = equation_size("{a+b+c+d} over {2}", 1000)
    lw, lh, lb = equation_size("a+b+c+d", 1000)
    assert fw < 1.4 * lw and fh > 1.8 * lh and fb < lb


def test_equation_markup_matches_hangul() -> None:
    run = etree.fromstring(equation_run_xml("a <= b & c", 7, "3", base_unit=1100))
    eq = run.find(f"{_HP}equation")
    assert run.get("charPrIDRef") == "3"
    assert eq.get("baseUnit") == "1100" and eq.get("font") == "HancomEQN"
    assert eq.get("version") == "Equation Version 60"
    assert eq.find(f"{_HP}pos").get("treatAsChar") == "1"
    assert eq.findtext(f"{_HP}script") == "a <= b & c"
    w, h = int(eq.find(f"{_HP}sz").get("width")), int(eq.find(f"{_HP}sz").get("height"))
    assert w > 0 and h > 0


# --------------------------------------------------------------------------- #
# write / extract / check
# --------------------------------------------------------------------------- #
MD = r"""# 수식

모형은 $y_t = \mu + \varepsilon_t$ 이며 가격 $5 and $10 은 그대로임.

$$
\Phi(B^s)\phi(B)(1-B)^d y_t = \Theta(B^s)\theta(B)\varepsilon_t
$$

| 지표 | 식 |
|---|---|
| MAPE | $\frac{100}{n}\sum_t \left|\frac{e_t}{y_t}\right|$ |
"""


def _sections(path: Path) -> list:
    with zipfile.ZipFile(path) as z:
        return [
            etree.fromstring(z.read(n))
            for n in sorted(z.namelist())
            if n.startswith("Contents/section")
        ]


def _scripts(path: Path) -> list[str]:
    return [s.text for r in _sections(path) for s in r.iter(f"{_HP}script")]


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_write_embeds_equations(tmp_path: Path) -> None:
    out = tmp_path / "eq.hwpx"
    res = fill_from_markdown(TPL, MD, output=out)
    scripts = _scripts(out)
    assert res.equations == len(scripts) == 3
    assert "y _{t} = mu + varepsilon _{t}" in scripts
    assert any(s.startswith("Phi ( B ^{s} )") for s in scripts)
    assert any("LEFT |" in s for s in scripts)  # the table cell kept its whole formula
    xml = "".join(etree.tostring(r, encoding="unicode") for r in _sections(out))
    assert "\\frac" not in xml and "$y_t" not in xml
    assert "$5 and $10" in xml  # prices untouched
    # ids are unique
    ids = [e.get("id") for r in _sections(out) for e in r.iter(f"{_HP}equation")]
    assert len(ids) == len(set(ids))
    assert diagnose_template(str(out))["equations"] == {"count": 3, "latex_text": []}


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_no_math_keeps_text(tmp_path: Path) -> None:
    out = tmp_path / "off.hwpx"
    res = fill_from_markdown(TPL, MD, output=out, math=False)
    assert res.equations == 0 and _scripts(out) == []
    left = diagnose_template(str(out))["equations"]["latex_text"]
    assert any("\\varepsilon_t" in t for t in left)


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_write_extract_round_trip(tmp_path: Path) -> None:
    a, b = tmp_path / "a.hwpx", tmp_path / "b.hwpx"
    fill_from_markdown(TPL, MD, output=a)
    md = extract_markdown(a)
    assert r"$\hwp{y _{t} = mu + varepsilon _{t}}$" in md
    assert "$$\\hwp{Phi" in md  # display paragraph → display block
    fill_from_markdown(TPL, md, output=b)
    assert _scripts(b) == _scripts(a)


@pytest.mark.skipif(not APPENDIX.is_file(), reason="fixture missing")
def test_extract_hangul_authored_equations() -> None:
    md = extract_markdown(APPENDIX)
    assert r"$\hwp{bar x}$" in md
    assert r"\hwp{bar x +- 1.96 times s / sqrt N = 68.0 +- 2.7}" in md
