"""Colour tokens and themes (R1): no colour literals outside the token blocks, the two light
blocks agree, on-air tokens stay dark, and every text colour passes WCAG AA in both themes."""

from __future__ import annotations

import re

import pytest

from core.config import ROOT

DOCS = ROOT / "docs"
CSS = (DOCS / "styles.css").read_text(encoding="utf-8")
INDEX = (DOCS / "index.html").read_text(encoding="utf-8")
SCRIPTS = sorted((DOCS / "js").glob("*.js"))

COLOUR = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?)\(")
TOKEN_BLOCK = re.compile(r"[^{}]*:root[^{}]*\{[^{}]*\}")
DECL = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b")
IDS = set(re.findall(r'\bid="([^"]+)"', INDEX))

AA = 4.5
SURFACES = ("bg", "card", "card-2", "inset")
TEXT = ("text", "muted", "accent", "verified", "corrected", "rejected", "home-text", "away-text")
ON_AIR = [  # (foreground, background) as drawn by the score bug, lower third and pitch tag
    ("air-text", "air-bg"),
    ("air-text", "air-panel"),
    ("air-muted", "air-panel-2"),
    ("air-verified", "air-panel"),
    ("air-accent", "air-panel"),
    ("air-bg", "air-text"),
    ("air-accent-ink", "air-accent"),
]


def _block(selector: str) -> str:
    start = CSS.index(selector + "{") + len(selector) + 1
    return CSS[start : CSS.index("}", start)]


def _tokens(block: str) -> dict[str, str]:
    return {name[2:]: value.upper() for name, value in DECL.findall(block)}


DARK = _tokens(_block(":root"))
LIGHT = _tokens(_block(':root[data-theme="light"]'))
THEMES = {"dark": DARK, "light": {**DARK, **LIGHT}}


def _luminance(hex_colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_styles_have_no_colour_outside_the_token_blocks() -> None:
    rest = TOKEN_BLOCK.sub("", re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL))
    assert not COLOUR.findall(rest), "colour literal in styles.css outside :root token blocks"


@pytest.mark.parametrize("path", [DOCS / "index.html", *SCRIPTS], ids=lambda p: p.name)
def test_page_and_scripts_have_no_colour_literals(path) -> None:  # type: ignore[no-untyped-def]
    text = path.read_text(encoding="utf-8")
    found = [m.group() for m in COLOUR.finditer(text) if m.group()[1:] not in IDS]
    assert not found, f"{path.name} hard-codes colours {found}; use a token"


def test_the_two_light_blocks_are_equal() -> None:
    media = _block(':root:not([data-theme="dark"])')
    assert media.strip() == _block(':root[data-theme="light"]').strip()


def test_light_theme_redefines_every_colour_except_on_air() -> None:
    themed = {k for k in DARK if not k.startswith("air-")}
    assert set(LIGHT) == themed
    assert not any(k.startswith("air-") for k in LIGHT)


def test_r1_values() -> None:
    expected = {
        "bg": ("#0B0D10", "#F5F6F8"),
        "card": ("#13161B", "#FFFFFF"),
        "card-2": ("#181C22", "#F8F9FB"),
        "line": ("#22272F", "#E2E5EA"),
        "text": ("#E9ECF1", "#14171C"),
        "muted": ("#8B94A1", "#5C6573"),
        "accent": ("#3B82F6", "#2563EB"),
        "verified": ("#22C55E", "#15803D"),
        "corrected": ("#F5B83D", "#B45309"),
        "rejected": ("#EF4444", "#B91C1C"),
        "home": ("#38BDF8", "#0284C7"),
        "away": ("#F472B6", "#DB2777"),
    }
    for name, (dark, light) in expected.items():
        assert (DARK[name], LIGHT[name]) == (dark, light), name


@pytest.mark.parametrize("theme", THEMES)
def test_text_colours_pass_wcag_aa(theme: str) -> None:
    tok = THEMES[theme]
    fails = [
        f"{fg} on {bg}: {contrast(tok[fg], tok[bg]):.2f}"
        for fg in TEXT
        for bg in SURFACES
        if contrast(tok[fg], tok[bg]) < AA
    ]
    if contrast(tok["accent-ink"], tok["accent"]) < AA:
        fails.append("accent-ink on accent")
    assert not fails, fails


def test_on_air_graphics_pass_wcag_aa() -> None:
    fails = [f"{fg} on {bg}" for fg, bg in ON_AIR if contrast(DARK[fg], DARK[bg]) < AA]
    assert not fails, fails


def test_light_team_colours_fail_as_text_so_text_uses_the_darker_variants() -> None:
    # Documents why --home-text and --away-text exist; if this starts failing, they can go.
    assert contrast(LIGHT["home"], LIGHT["bg"]) < AA
    assert contrast(LIGHT["away"], LIGHT["bg"]) < AA


def test_theme_script_runs_before_the_stylesheet() -> None:
    assert INDEX.index('<script src="js/theme.js"></script>') < INDEX.index('href="styles.css"')
    assert "localStorage" in (DOCS / "js" / "theme.js").read_text(encoding="utf-8")


def test_every_css_variable_used_is_defined() -> None:
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
    # The control room only: overlay.html and overlay.js have their own variables.
    css = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
    sources = {"styles.css": css, "index.html": INDEX} | {
        p.name: p.read_text(encoding="utf-8") for p in SCRIPTS if p.name != "overlay.js"
    }
    missing = {
        f"{name}: {var}"
        for name, text in sources.items()
        for var in re.findall(r"var\((--[\w-]+)", text) + re.findall(r"'(--[\w-]+)'", text)
        if var not in defined
    }
    assert not missing, sorted(missing)


# ---------- R4: glass on the score bug, lower third, evidence drawer and About dialog only ----------
GLASS = {".bug", ".lt", "#drawer", "#aboutdlg"}
PCT = re.compile(r"--(glass-[\w-]+)\s*:\s*(\d+)%")
NO_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
WHITE, BLACK = "#FFFFFF", "#000000"


def _pct(block: str) -> dict[str, float]:
    return {name: int(value) / 100 for name, value in PCT.findall(block)}


GLASS_PCT = {
    "dark": _pct(_block(":root")),
    "light": {**_pct(_block(":root")), **_pct(_block(':root[data-theme="light"]'))},
}


def over(top: str, alpha: float, under: str) -> str:
    """Source-over compositing in sRGB, as the browser draws a translucent panel."""
    a, b = ([int(c[i : i + 2], 16) for i in (1, 3, 5)] for c in (top, under))
    return "#" + "".join(
        f"{round(alpha * x + (1 - alpha) * y):02X}" for x, y in zip(a, b, strict=True)
    )


def _selectors(pattern: str) -> set[str]:
    rules = re.findall(r"([^{}]+)\{[^{}]*" + pattern, NO_COMMENTS)
    return {s.strip() for rule in rules for s in rule.split(",")}


def test_glass_is_on_the_four_overlays_and_nothing_else() -> None:
    assert _selectors(r"(?<!-webkit-)backdrop-filter:blur") == GLASS
    assert _selectors(r"-webkit-backdrop-filter:blur") == GLASS


@pytest.mark.parametrize("query", ["@supports not", "@media (prefers-reduced-transparency:reduce)"])
def test_glass_falls_back_to_solid_panels(query: str) -> None:
    start = NO_COMMENTS.index(query)
    block = NO_COMMENTS[start : NO_COMMENTS.index("}}", start)]
    solid = {
        s.strip()
        for rule in re.findall(r"\{?([^{}]+)\{background:var\(--[\w-]+\)", block)
        for s in rule.split(",")
    }
    assert solid == GLASS, f"{query} must give all four overlays a solid token background"


def test_coloured_and_muted_on_air_text_sits_on_solid_strips() -> None:
    # Accent, verified and muted text need 82-97% opacity over a white pitch line, so these
    # strips stay solid; only the white text sits directly on the glass.
    for selector, token in [
        (".bug .m", "air-panel-2"),
        (".lt .k", "air-panel"),
        (".lt .f", "air-panel-2"),
    ]:
        assert f"background:var(--{token})" in _block(selector), selector


@pytest.mark.parametrize("theme", THEMES)
def test_on_air_glass_text_passes_aa_over_the_brightest_pitch_mark(theme: str) -> None:
    # The stage behind the bug and lower third holds nothing brighter than white (pitch lines);
    # blurring only averages it, so white is the worst case in both themes.
    tok, alpha = THEMES[theme], GLASS_PCT[theme]["glass-air"]
    assert contrast(tok["air-text"], over(tok["air-bg"], alpha, WHITE)) >= AA


@pytest.mark.parametrize("theme", THEMES)
def test_glass_dialog_text_passes_aa_over_any_page(theme: str) -> None:
    # Behind a dialog: the page, dimmed by dialog::backdrop. Check both extremes of the page.
    tok, alpha = THEMES[theme], GLASS_PCT[theme]["glass-panel"]
    dim = (
        int(
            re.search(
                r"dialog::backdrop\{background:color-mix\(in srgb,var\(--shadow\) (\d+)%",
                NO_COMMENTS,
            ).group(1)
        )
        / 100
    )  # type: ignore[union-attr]
    fails = [
        f"{fg} over {page}: {contrast(tok[fg], panel):.2f}"
        for page in (WHITE, BLACK)
        for panel in [over(tok["card"], alpha, over(tok["shadow"], dim, page))]
        for fg in TEXT
        if contrast(tok[fg], panel) < AA
    ]
    assert not fails, fails
