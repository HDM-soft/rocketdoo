"""Invariants for the color tokens in `rocketdoo/gui/static/index.html`.

The GUI is a single-file Vue 3 SPA with all CSS in one `<style>` block and no
build step. Colors live as CSS custom properties declared in three theme
blocks: the light-mode default `:root`, `@media (prefers-color-scheme: dark)`
and `:root[data-theme="dark"]`. RGB triplets used for translucent overlays
(`rgba(var(--br-rgb), .3)`) are declared the same way, as a component token
consumed by `rgba()`. These tests pin down the structure so a future edit
can't reintroduce a literal hex color, a hand-written `rgba()`/`rgb()`/
`hsl()`/`hsla()`, or drift the three blocks apart.

The only literals allowed to remain outside the theme blocks are the
log-level colors inside the Docker log terminal (`.log-term`, `.log-err`,
`.log-warn`): that surface is exempt from theming because it renders real
terminal output at high contrast in both themes.

A separate test guards RF6 (every element operable by keyboard has a visible
focus indicator): anything wired to `@click` has to be a tag the browser
already puts in the tab order (`<button>`, or `<a href="...">`), never a bare
`<div>`/`<span>`/`<a>` without `href`. Those never receive focus at all, so no
amount of `:focus-visible` CSS makes them keyboard-operable.
"""

import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "rocketdoo" / "gui" / "static" / "index.html"

HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}")
# A color function is only a literal when its first argument is a number: `rgba(var(--x),.4)`
# is a token reference and must not match, `rgba(26,61,204,.4)` is the literal this guards
# against.
COLOR_FUNC_LITERAL_RE = re.compile(r"(?:rgba?|hsla?)\(\s*\d[^)]*\)")
# A CSS named color used directly as a property value (`color:white`), not `transparent`
# (which has no theme-dependent equivalent) and not a class name like `badge-green` or a
# property name like `white-space` — both excluded by requiring the colon right before it.
NAMED_COLOR_LITERAL_RE = re.compile(r":\s*(?:white|black|red|green|blue|yellow|purple|orange|gray|grey)\b")
COLOR_LITERAL_RE = re.compile(f"{HEX_RE.pattern}|{COLOR_FUNC_LITERAL_RE.pattern}|{NAMED_COLOR_LITERAL_RE.pattern}")
VAR_USE_RE = re.compile(r"var\(--([a-zA-Z0-9-]+)\)")
TOKEN_DECL_RE = re.compile(r"--([a-zA-Z0-9-]+):")
TOKEN_VALUE_RE = re.compile(r"--([a-zA-Z0-9-]+):\s*(#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3})\b")

TERMINAL_HEX_EXCEPTIONS = {"#7EC87E", "#F87171", "#FCD34D"}

# Every `var(--on-br)` use has to be manually confirmed to render on top of a --br-colored
# surface (a button fill, an active toggle track, or the logo gradient) — that pairing is the
# only thing that makes "on-br" text/icon color legible, and --on-br is #FFFFFF in every theme
# block, so a wrong pairing (text color identical to its own background, like `.logo-text`
# once was: --on-br on the sidebar surface --s, not on --br) passes every other test in this
# file silently, in every theme, because the color literally never changes to trip a contrast
# check. A whitelist by exact snippet, reviewed by hand, closes it the same way
# TERMINAL_HEX_EXCEPTIONS closes the terminal's exception: any new use has to be added here
# explicitly, with the surface it sits on written out for the next reviewer.
ON_BR_USAGES = {
    ".btn-primary { background: var(--br); color: var(--on-br); }": "primary button text, fill is --br",
    "input:checked ~ .toggle-track::before { transform: translateX(18px); background: var(--on-br); }": (
        "checked toggle knob, sits on the track, which is --br once checked"
    ),
    '<span v-html="I.rocket" style="width:18px;height:18px;color:var(--on-br);"></span>': (
        "logo rocket icon, .logo-icon background gradient starts at --br"
    ),
}

# Same shape of bug as --on-br: --term-t is the one text color confirmed legible on --term-b in
# every theme (identical value in all three blocks by design, per RF3's terminal exception), so
# any other token used as text on an inline background:var(--term-b) risks failing contrast in
# at least one theme without ever changing value to trip a test — exactly how --ok, --lk and
# --su each slipped through against --term-b in light mode (T2/T3) before being switched to
# --term-t. Unlike --on-br this doesn't get enumerated by hand: every current use follows the
# same inline `style="...background:var(--term-b)...color:var(--x)..."` shape, so the surface
# and the color are checked together wherever they occur, with no whitelist to maintain.
INLINE_STYLE_RE = re.compile(r'style="([^"]*)"')

# Matches one tag's opening `<name ...>`, treating a whole quoted attribute value (which
# may itself contain a stray `>` from a JS expression like `running>0`) as a single unit
# so it can't be mistaken for the tag's real closing bracket. re.S lets the attribute list
# span multiple lines, since `@click` is often on a line of its own.
TAG_OPEN_RE = re.compile(r"<(\w+)\b((?:\"[^\"]*\"|'[^']*'|[^\">])*)>", re.S)
NON_INTERACTIVE_CLICK_TAGS = {"div", "span", "a"}

# Every pair mirrors a real foreground/background combination rendered in index.html:
# body/secondary/tertiary text over the page and over cards, links over both, button
# text over the brand button, and the semantic/status tokens over the card surface
# where they render as badge text (badge-green/red/yellow/gray/purple, and the
# dashboard/module/instance status pills). WCAG 2.1 requires 4.5:1 for this kind of
# normal-size text in both themes.
#
# The terminal is excluded on purpose: `.log-term`/`.log-err`/`.log-warn` keep the
# literal hex colors from TERMINAL_HEX_EXCEPTIONS (RF3's own carve-out, checked by
# test_terminal_hex_exceptions_are_only_the_log_level_colors), and `.code-block` plus
# every `<code>` snippet on the Help page render `--term-t` on `--term-b`, a pair that
# is identical in both themes by construction (both tokens keep the same value in
# :root, the dark media query and [data-theme=dark]), so there is nothing theme-
# dependent left to audit there.
CONTRAST_PAIRS = [
    ("tx", "b", 4.5),
    ("tx", "s", 4.5),
    ("mu", "b", 4.5),
    ("mu", "s", 4.5),
    ("su", "b", 4.5),
    ("su", "s", 4.5),
    ("lk", "b", 4.5),
    ("lk", "s", 4.5),
    ("on-br", "br", 4.5),
    ("ok", "s", 4.5),
    ("wn", "s", 4.5),
    ("er", "s", 4.5),
    ("gy", "s", 4.5),
    ("pu", "s", 4.5),
]


def _text():
    return INDEX_HTML.read_text()


def _style_block(text):
    match = re.search(r"<style>(.*?)</style>", text, re.S)
    assert match, "expected a single <style> block in index.html"
    return match


def _script_block(text):
    match = re.search(r"<script>(.*)</script>", text, re.S)
    assert match, "expected a single <script> block in index.html"
    return match


def _root_light_match(style):
    match = re.search(r":root\s*\{(.*?)\n\}", style, re.S)
    assert match, "expected a top-level :root block declaring the light palette"
    return match


def _media_dark_match(style):
    match = re.search(
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*"
        r':root:not\(\[data-theme="light"\]\)\s*\{(.*?)\n\s*\}\n\}',
        style,
        re.S,
    )
    assert match, 'expected @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ... } }'
    return match


def _attr_dark_match(style):
    match = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}', style, re.S)
    assert match, 'expected a :root[data-theme="dark"] block'
    return match


def _tokens(block_text):
    return set(TOKEN_DECL_RE.findall(block_text))


def _token_values(block_text):
    return dict(TOKEN_VALUE_RE.findall(block_text))


def _relative_luminance(hexval):
    hexval = hexval.lstrip("#")
    if len(hexval) == 3:
        hexval = "".join(c * 2 for c in hexval)
    r, g, b = (int(hexval[i : i + 2], 16) for i in (0, 2, 4))

    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(hex_a, hex_b):
    luminance_a = _relative_luminance(hex_a)
    luminance_b = _relative_luminance(hex_b)
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def test_every_used_token_is_declared_in_root():
    text = _text()
    style = _style_block(text).group(1)
    declared = _tokens(_root_light_match(style).group(1))
    used = set(VAR_USE_RE.findall(text))
    missing = used - declared
    assert not missing, f"var(--x) used but never declared in :root: {sorted(missing)}"


def test_theme_blocks_declare_the_same_token_set():
    style = _style_block(_text()).group(1)
    light = _tokens(_root_light_match(style).group(1))
    media_dark = _tokens(_media_dark_match(style).group(1))
    attr_dark = _tokens(_attr_dark_match(style).group(1))
    assert light, "light :root declared no tokens"
    assert light == media_dark, "media dark block drifted from :root's token set"
    assert light == attr_dark, "[data-theme=dark] block drifted from :root's token set"


def test_no_color_literals_outside_the_three_theme_blocks():
    text = _text()
    style_match = _style_block(text)
    style = style_match.group(1)

    theme_spans = sorted(
        (
            _root_light_match(style).span(),
            _media_dark_match(style).span(),
            _attr_dark_match(style).span(),
        ),
        reverse=True,
    )
    style_outside_themes = style
    for start, end in theme_spans:
        style_outside_themes = style_outside_themes[:start] + style_outside_themes[end:]

    outside_style_tag = text[: style_match.start()] + text[style_match.end() :]
    haystack = style_outside_themes + outside_style_tag

    found = set(COLOR_LITERAL_RE.findall(haystack))
    unexpected = found - TERMINAL_HEX_EXCEPTIONS
    assert not unexpected, (
        f"color literals (hex or rgba/rgb/hsl/hsla with numeric arguments) found outside the "
        f"theme blocks and the terminal exceptions: {sorted(unexpected)}"
    )


def test_terminal_hex_exceptions_are_only_the_log_level_colors():
    style = _style_block(_text()).group(1)
    for hexval in TERMINAL_HEX_EXCEPTIONS:
        assert re.search(rf"\.log-(?:term|err|warn)\s*\{{[^}}]*{re.escape(hexval)}", style), (
            f"{hexval} is only allowed inside .log-term/.log-err/.log-warn"
        )


def test_contrast_pairs_meet_wcag_thresholds():
    style = _style_block(_text()).group(1)
    blocks = {
        ":root (light)": _token_values(_root_light_match(style).group(1)),
        "@media prefers-color-scheme: dark": _token_values(_media_dark_match(style).group(1)),
        ':root[data-theme="dark"]': _token_values(_attr_dark_match(style).group(1)),
    }
    failures = []
    for block_name, values in blocks.items():
        for fg, bg, threshold in CONTRAST_PAIRS:
            ratio = _contrast_ratio(values[fg], values[bg])
            if ratio < threshold:
                failures.append(
                    f"{block_name}: --{fg} ({values[fg]}) on --{bg} ({values[bg]}) = {ratio:.2f}:1, needs {threshold}:1"
                )
    assert not failures, "\n".join(failures)


def test_on_br_usages_are_all_confirmed_to_sit_on_br():
    text = _text()
    total = text.count("var(--on-br)")
    accounted = 0
    for snippet, surface in ON_BR_USAGES.items():
        count = text.count(snippet)
        assert count, f"reviewed --on-br usage disappeared or changed shape ({surface}): {snippet!r}"
        accounted += count
    assert accounted == total, (
        f"found {total} var(--on-br) uses but only {accounted} are in the reviewed whitelist; "
        "a new --on-br use was added without confirming it renders on a --br surface — add it "
        "to ON_BR_USAGES with the surface it sits on, or use --tx/--lk if it doesn't sit on --br"
    )


def test_no_other_text_color_shares_an_inline_term_b_background():
    text = _text()
    violations = []
    for match in INLINE_STYLE_RE.finditer(text):
        declaration = match.group(1).replace(" ", "")
        if "background:var(--term-b)" not in declaration:
            continue
        colors = set(re.findall(r"color:var\(--([a-zA-Z0-9-]+)\)", declaration))
        bad = colors - {"term-t"}
        if bad:
            line_no = text[: match.start()].count("\n") + 1
            violations.append(f"line {line_no}: color(s) {sorted(bad)} on the same background:var(--term-b)")
    assert not violations, "text color other than --term-t found on an inline background:var(--term-b):\n" + "\n".join(
        violations
    )


def test_click_handlers_only_on_natively_interactive_tags():
    script = _script_block(_text()).group(1)
    violations = []
    for match in TAG_OPEN_RE.finditer(script):
        tag, attrs = match.group(1), match.group(2)
        if tag not in NON_INTERACTIVE_CLICK_TAGS or "@click" not in attrs:
            continue
        if tag == "a" and re.search(r"\bhref\s*=", attrs):
            continue
        line_no = script[: match.start()].count("\n") + 1
        violations.append(f"line {line_no}: <{tag} {attrs.strip()[:60]!r}...>")
    assert not violations, (
        "elements wired to @click must be natively focusable (<button> or <a href>), "
        "otherwise :focus-visible never triggers and the keyboard can't reach them:\n" + "\n".join(violations)
    )
