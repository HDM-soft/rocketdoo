"""Invariants for the color tokens in `rocketdoo/gui/static/index.html`.

The GUI is a single-file Vue 3 SPA with all CSS in one `<style>` block and no
build step. Colors live as CSS custom properties declared in three theme
blocks: the light-mode default `:root`, `@media (prefers-color-scheme: dark)`
and `:root[data-theme="dark"]`. RGB triplets used for translucent overlays
(`rgba(var(--br-rgb), .3)`) are declared the same way, as a component token
consumed by `rgba()`. These tests pin down the structure so a future edit
can't reintroduce a literal hex color, a hand-written `rgba()`/`rgb()`/
`hsl()`/`hsla()`, a CSS named color, or drift the three blocks apart.

The only literals allowed to remain outside the theme blocks are the
log-level colors inside the Docker log terminal (`.log-term`, `.log-err`,
`.log-warn`): that surface is exempt from theming because it renders real
terminal output at high contrast in both themes. `test_terminal_hex_exceptions_are_only_the_log_level_colors`
checks that exclusively, by counting occurrences, not just finding one.

Contrast is checked two ways. `CONTRAST_PAIRS` covers text/icons rendered
directly on a surface. `TINTED_CONTRAST_PAIRS` covers badges/pills, which
never render on the plain surface: the text sits on its own translucent tint
(`rgba(var(--x-rgb), alpha)`) composited over the surface, and RF3 requires
4.5:1 against that *real*, composited background, not the surface alone.
`BORDER_CONTRAST_PAIRS` covers WCAG 1.4.11's non-text 3:1 for the few borders
that are the only thing identifying a control (`.input`, `.btn-ghost`, the
switch's off-state fill) — plain card/table dividers don't need this, they
are not the sole way to identify a component.

A separate test guards RF6 (every element operable by keyboard has a visible
focus indicator): anything wired to `@click` has to be a tag the browser
already puts in the tab order (`<button>`, or `<a href="...">`), never a bare
`<div>`/`<span>`/`<a>` without `href`. Those never receive focus at all, so no
amount of `:focus-visible` CSS makes them keyboard-operable.
"""

import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "rocketdoo" / "gui" / "static" / "index.html"

HEX_RE = re.compile(r"(?<!&)#[0-9A-Fa-f]{3,8}")
# A color function is only a literal when its first argument is a number: `rgba(var(--x),.4)`
# is a token reference and must not match, `rgba(26,61,204,.4)` is the literal this guards
# against.
COLOR_FUNC_LITERAL_RE = re.compile(r"(?:rgba?|hsla?)\(\s*\d[^)]*\)")
# The full CSS Color Module Level 4 named-color list (148 keywords), minus `transparent` and
# `currentcolor`, which are allowed. Matched with a hyphen-aware boundary on both sides so it
# never fires inside a class name (`badge-green`, `dot-red`), a property name (`white-space`),
# or mid-word (`instant`, `constant`) — only a standalone color keyword, wherever it appears:
# `border: 1px solid white`, a `linear-gradient(...)` stop, or an SVG `fill="white"`. This is
# deliberately a "reject everything that looks like a raw color" list rather than a "require a
# colon right before it" pattern: the latter is what let `border:1px solid white` and gradient
# stops slip past an earlier, narrower version of this same check.
_CSS_NAMED_COLORS = frozenset(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson
cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray
darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick
floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray grey green greenyellow
honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink
lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow
lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue
mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid
palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen
seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal
thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split()
)
NAMED_COLOR_LITERAL_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(sorted(_CSS_NAMED_COLORS, key=len, reverse=True)) + r")(?![\w-])",
    re.IGNORECASE,
)
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

# Text/icons rendered directly on a surface: body/secondary/tertiary text over the page and
# over cards, links over both, button text over the brand button, and the semantic/status
# tokens over the card surface where they render as plain text (not badge text — see
# TINTED_CONTRAST_PAIRS for that). WCAG 2.1 requires 4.5:1 for this kind of normal-size text in
# both themes.
#
# The terminal is excluded on purpose: `.log-term`/`.log-err`/`.log-warn` keep the literal hex
# colors from TERMINAL_HEX_EXCEPTIONS (RF3's own carve-out), and `.code-block` plus every
# `<code>` snippet on the Help page render `--term-t` on `--term-b`, a pair that is identical in
# both themes by construction, so there is nothing theme-dependent left to audit there.
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

# Badge/pill text: (fg_token, tint_token, alpha, surface_token, threshold). The tint is what
# `.badge-*` actually paints, `rgba(var(--tint-rgb), alpha)`, composited over the surface — this
# is the badge's real background, not the plain surface. tint_token differs from fg_token only
# for badge-blue/v3-badge, whose fill is `--br-rgb` (the brand fill) even though the text
# renders in `--lk` (identical to --br in the light theme, not in the dark one).
TINTED_CONTRAST_PAIRS = [
    ("ok", "ok", 0.18, "s", 4.5),  # .badge-green
    ("wn", "wn", 0.18, "s", 4.5),  # .badge-yellow
    ("er", "er", 0.18, "s", 4.5),  # .badge-red
    ("pu", "pu", 0.22, "s", 4.5),  # .badge-purple
    ("gy", "gy", 0.18, "s", 4.5),  # .badge-gray
    ("lk", "br", 0.22, "s", 4.5),  # .badge-blue
    ("lk", "br", 0.30, "s", 4.5),  # .v3-badge
]

# WCAG 1.4.11 non-text contrast: "visual information required to identify user interface
# components and states". --ln2 is T1's control-border token; these are the cases where the
# border (or, for the switch's off state, the fill itself) is the *only* thing that identifies
# the control — `.input` and `.btn-ghost` sit on the exact same surface as their container, and
# an unchecked switch has no border at all. Plain card/table dividers (--ln) are not the sole
# way to identify a component, so WCAG 1.4.11 does not reach them.
BORDER_CONTRAST_PAIRS = [
    ("ln2", "s", 3.0),
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


def _theme_blocks(style):
    """Token values for the three theme blocks, keyed by a human-readable label."""
    return {
        ":root (light)": _token_values(_root_light_match(style).group(1)),
        "@media prefers-color-scheme: dark": _token_values(_media_dark_match(style).group(1)),
        ':root[data-theme="dark"]': _token_values(_attr_dark_match(style).group(1)),
    }


def _style_outside_theme_blocks(text):
    """The `<style>` body and the rest of the document, with the three theme blocks removed."""
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
    return style_outside_themes, outside_style_tag


def _hex_to_rgb(hexval):
    hexval = hexval.lstrip("#")
    if len(hexval) == 3:
        hexval = "".join(c * 2 for c in hexval)
    return tuple(int(hexval[i : i + 2], 16) for i in (0, 2, 4))


def _blend(fg_hex, alpha, bg_hex):
    """`fg_hex` at `alpha` opacity composited over the opaque `bg_hex`, as the browser paints it."""
    fg = _hex_to_rgb(fg_hex)
    bg = _hex_to_rgb(bg_hex)
    blended = (fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))
    return "#" + "".join(f"{round(c):02X}" for c in blended)


def _relative_luminance(hexval):
    r, g, b = _hex_to_rgb(hexval)

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
    style_outside_themes, outside_style_tag = _style_outside_theme_blocks(_text())
    haystack = style_outside_themes + outside_style_tag

    found = set(COLOR_LITERAL_RE.findall(haystack))
    unexpected = found - TERMINAL_HEX_EXCEPTIONS
    assert not unexpected, (
        f"color literals (hex, rgba/rgb/hsl/hsla with numeric arguments, or a CSS named color) "
        f"found outside the theme blocks and the terminal exceptions: {sorted(unexpected)}"
    )


def test_terminal_hex_exceptions_are_only_the_log_level_colors():
    style_outside_themes, outside_style_tag = _style_outside_theme_blocks(_text())
    haystack = style_outside_themes + outside_style_tag

    failures = []
    for hexval in TERMINAL_HEX_EXCEPTIONS:
        total = haystack.count(hexval)
        within_selectors = len(re.findall(rf"\.log-(?:term|err|warn)\s*\{{[^}}]*{re.escape(hexval)}", style_outside_themes))
        if total != within_selectors:
            failures.append(
                f"{hexval} appears {total} time(s) total but only {within_selectors} inside "
                ".log-term/.log-err/.log-warn — it is used somewhere else too"
            )
    assert not failures, "\n".join(failures)


def test_contrast_pairs_meet_wcag_thresholds():
    style = _style_block(_text()).group(1)
    failures = []
    for block_name, values in _theme_blocks(style).items():
        for fg, bg, threshold in CONTRAST_PAIRS:
            ratio = _contrast_ratio(values[fg], values[bg])
            if ratio < threshold:
                failures.append(
                    f"{block_name}: --{fg} ({values[fg]}) on --{bg} ({values[bg]}) = {ratio:.2f}:1, needs {threshold}:1"
                )
    assert not failures, "\n".join(failures)


def test_tinted_badge_pairs_meet_wcag_thresholds():
    style = _style_block(_text()).group(1)
    failures = []
    for block_name, values in _theme_blocks(style).items():
        for fg, tint, alpha, surface, threshold in TINTED_CONTRAST_PAIRS:
            effective_bg = _blend(values[tint], alpha, values[surface])
            ratio = _contrast_ratio(values[fg], effective_bg)
            if ratio < threshold:
                failures.append(
                    f"{block_name}: --{fg} on {alpha:.0%} --{tint} over --{surface} "
                    f"({effective_bg}) = {ratio:.2f}:1, needs {threshold}:1"
                )
    assert not failures, "\n".join(failures)


def test_border_pairs_meet_wcag_non_text_threshold():
    style = _style_block(_text()).group(1)
    failures = []
    for block_name, values in _theme_blocks(style).items():
        for fg, bg, threshold in BORDER_CONTRAST_PAIRS:
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
