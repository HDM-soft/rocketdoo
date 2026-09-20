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

# i18n (T4). `L = { es: {...}, en: {...} }` is the whole dictionary — no vue-i18n, no build step,
# per RNF2. `TRANSLATION_CALL_RE` matches both `$t('key'` (used inside `template:` strings) and the
# bare `t('key'` used from plain JS (the nav array and `statusLabel()`, built outside the render
# context where `$t` — a globalProperties method — is not in scope, but the module-level `t()`
# function it wraps is).
LANG_DICT_RE = re.compile(r"const L = \{\n  es: \{(.*?)\n  \},\n  en: \{(.*?)\n  \},\n\}", re.S)
# Requires the backtick right after the colon, so a continuation line of a multi-line value
# (e.g. one starting with "Nota:"/"Note:") can never be mistaken for a new key — that line has
# no backtick of its own right after its colon (T4 review, M2).
DICT_KEY_RE = re.compile(r"^\s*([a-zA-Z0-9_]+):\s*`", re.M)
# Every `key: `value`` pair, key -> value, used to compare interpolation placeholders per key
# (see test_i18n_interpolation_placeholders_match_call_sites). Non-greedy is safe here because,
# same as DICT_KEY_RE, no dictionary value contains a literal backtick.
DICT_ENTRY_RE = re.compile(r"([a-zA-Z0-9_]+):\s*`(.*?)`,", re.S)
TRANSLATION_CALL_RE = re.compile(r"(?<!\w)\$?t\('([a-zA-Z0-9_]+)'")
# A call site that also passes an interpolation object: `$t('key', {a: x, b: y})`.
TRANSLATION_CALL_WITH_VARS_RE = re.compile(r"(?<!\w)\$?t\('([a-zA-Z0-9_]+)',\s*\{([^}]*)\}")
INTERP_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
# `(?:\\.|[^`\\])*` instead of the naive `.*?`: a template that ever needs an escaped backtick
# (`` \` ``) no longer truncates the capture there and silently stops auditing the rest of that
# component (T4 review, M3). JS template literals can't contain an unescaped backtick anyway, so
# this is exact, not just "less wrong".
TEMPLATE_LITERAL_RE = re.compile(r"template:\s*`((?:\\.|[^`\\])*)`", re.S)
INNER_TAG_RE = re.compile(r"<(?:\"[^\"]*\"|'[^']*'|[^>])*>", re.S)
MUSTACHE_RE = re.compile(r"\{\{.*?\}\}", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
HTML_ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
# `alt` added per T4 review M14 — no `<img>` uses it today, but the other three attributes were
# the only ones audited, so a future `alt=` (or any other static-text attribute) would have
# slipped through unnoticed.
STATIC_TEXT_ATTR_RE = re.compile(r'(?<![:\w])(placeholder|title|aria-label|alt)="([^"]*)"')
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}")
# A handful of `placeholder="..."` values are example data, not interface text: a sample URL, a
# domain, an email, or a bare version/port number. They don't need `$t(...)` because there's
# nothing to translate — "github.com" isn't English or Spanish. Anything with a space, or that
# doesn't fully match one of these shapes, still goes through the phrase-level check below.
EXAMPLE_VALUE_RE = re.compile(r"^(https?://\S+|[\w.-]+@[\w.-]+\.\w+|[\w-]+(?:\.[\w-]+)+|\d+(?:\.\d+)?)$")

# Every whole chunk of text that is allowed to survive outside `$t(...)`/mustache interpolation in
# a template, because RF8 explicitly excludes it from translation: product/tool proper nouns, and
# literal `rkd`/`docker`/`gitman` commands or filesystem paths shown as an example. This used to be
# a set of individual words (`ALLOWED_LITERAL_WORDS`), which let any recombination of those words
# survive as a sentence of its own (`rkd up to deploy web` passed, T4 review B2) — comparing the
# *entire* stripped chunk against this list closes that. Derived by hand from every chunk that
# actually survives stripping today (see the two tests below); a new literal chunk introduced in
# either language will not equal any entry here and fails the test, forcing it through `$t(...)`.
ALLOWED_LITERAL_PHRASES = {
    "$ rkd init",
    "$ rkd instance init",
    "$ rkd scaffold",
    "Community",
    "Enterprise —",
    "Gitman",
    "Mailpit",
    "Odoo",
    "Odoo:",
    "PG",
    "RKD",
    "Rocketdoo",
    "SMTP",
    "Traefik",
    "cd /path/to/project",
    "docker",
    "external_addons/",
    "gitman update",
    "native",
    "rkd build --rebuild",
    "rkd deploy init",
    "rkd deploy list-modules",
    "rkd deploy run -t prod",
    "rkd docker restart web",
    "rkd down",
    "rkd gui",
    "rkd info",
    "rkd init",
    "rkd instance deploy --env stage",
    "rkd instance init",
    "rkd logs web -f",
    "rkd mail on/off",
    "rkd pack",
    "rkd restart",
    "rkd scaffold",
    "rkd status",
    "rkd traefik on/off",
    "rkd unpack",
    "rkd up -d",
    "rkd-shared.json",
    "token",
}

# Sinks where interface text is composed from plain JS, outside any `template:` string, and so
# invisible to the two tests above by construction — the exact gap that let 48 `notify()` calls
# (plus `confirm()`, `output.value`, and the two WebSocket components' own connection messages)
# stay in English under a Spanish UI (T4 review B1/B2). `notify(`/`confirm(`/`alert(` are function
# calls; `lines.value.push(` is the one non-object `.push()` call in the file — every other
# `.push()` call site pushes a variable, a spread, or an object, never a literal (verified by hand;
# see the review). `output.value =` is a plain assignment, not a call.
SINK_CALL_RE = re.compile(r"\b(?:notify|confirm|alert)\(|lines\.value\.push\(")
OUTPUT_VALUE_RE = re.compile(r"output\.value\s*=\s*([^;\n]*)")
# A generic net for any other text-returning helper following the same shape as `updateReason()`
# (three `return '...'` sentences that predate this sink list). Restricted to literals containing
# a space so it never trips on the file's many single-token `return 'running'` / `return
# 'badge-green'` style enum helpers (`cstatus`, `dotClass`, `statusBadgeClass`, `lineClass`...),
# none of which are user-facing text — RF8 doesn't reach them and none of them contain a space.
GENERIC_RETURN_LITERAL_RE = re.compile(r"return\s+('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)")
LITERAL_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", re.S)
INTERPOLATION_SPAN_RE = re.compile(r"\$\{.*?\}", re.S)
# The only literal words `notify()`'s own `type` parameter ever takes — enum values, not text.
NOTIFY_TYPE_WORDS = {"ok", "err"}

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


def _lang_dicts(text):
    match = LANG_DICT_RE.search(text)
    assert match, "expected `const L = { es: {...}, en: {...} }` in index.html"
    return {"es": match.group(1), "en": match.group(2)}


def _dict_keys(block_text):
    return set(DICT_KEY_RE.findall(block_text))


def test_i18n_dictionaries_have_matching_keys():
    dicts = _lang_dicts(_text())
    es_keys, en_keys = _dict_keys(dicts["es"]), _dict_keys(dicts["en"])
    assert es_keys, "L.es declared no keys"
    assert es_keys == en_keys, (
        f"L.es and L.en have different keys — only in es: {sorted(es_keys - en_keys)}, only in en: {sorted(en_keys - es_keys)}"
    )


def test_i18n_no_unused_or_undeclared_translation_keys():
    text = _text()
    script = _script_block(text).group(1)
    declared = _dict_keys(_lang_dicts(text)["en"])
    used = set(TRANSLATION_CALL_RE.findall(script))
    assert not (used - declared), f"$t()/t() called with a key never declared in L: {sorted(used - declared)}"
    assert not (declared - used), f"declared in L but never called with $t()/t(): {sorted(declared - used)}"


def _templates(text):
    script = _script_block(text).group(1)
    templates = TEMPLATE_LITERAL_RE.findall(script)
    assert len(templates) >= 10, "expected one `template: `...`` string per component"
    return templates


def test_i18n_no_static_text_attributes_outside_whitelist():
    """`placeholder`/`title`/`aria-label`/`alt` bound as plain strings (not `:placeholder=...`)
    have to be either `$t(...)` already, a literal example value (URL, port, domain, email) with
    no translatable word in it, or an exact match in `ALLOWED_LITERAL_PHRASES` — never a
    hardcoded instruction or label, and never salvaged by whitelisting one of its words on its
    own (T4 review B2: a value is checked whole, not word by word)."""
    violations = []
    for tpl in _templates(_text()):
        for match in STATIC_TEXT_ATTR_RE.finditer(tpl):
            attr, value = match.group(1), match.group(2)
            if EXAMPLE_VALUE_RE.match(value):
                continue
            if not WORD_RE.search(value):
                continue
            if value in ALLOWED_LITERAL_PHRASES:
                continue
            violations.append(f'{attr}="{value}" (not $t(...) and not in ALLOWED_LITERAL_PHRASES)')
    assert not violations, "static text attribute(s) with untranslated content:\n" + "\n".join(violations)


# Splits a template on every tag, `{{ }}` interpolation, HTML comment and entity, keeping the
# text in between as a list of separate chunks (rather than collapsing them all to blank spaces
# and re-tokenizing into a word soup, which is what let unrelated words from different chunks
# recombine into a sentence that was never actually written anywhere, like "rkd up to deploy web"
# — T4 review B2).
TEMPLATE_CHUNK_SPLIT_RE = re.compile(
    "(?:" + "|".join([HTML_COMMENT_RE.pattern, MUSTACHE_RE.pattern, INNER_TAG_RE.pattern, HTML_ENTITY_RE.pattern]) + ")",
    re.S,
)


def test_i18n_no_untranslated_text_between_tags():
    """Every chunk of text left after stripping tags/interpolation/comments/entities out of a
    template has to be either empty, free of any translatable word (pure punctuation), or an
    *exact* match in `ALLOWED_LITERAL_PHRASES` — compared as the whole chunk, not word by word.
    Anything else is interface text that was never routed through `$t(...)` — the RF7 gap this
    task exists to close.
    """
    violations = []
    for tpl in _templates(_text()):
        for chunk in TEMPLATE_CHUNK_SPLIT_RE.split(tpl):
            collapsed = re.sub(r"\s+", " ", chunk).strip()
            if not collapsed or not WORD_RE.search(collapsed):
                continue
            if collapsed in ALLOWED_LITERAL_PHRASES:
                continue
            violations.append(f"{collapsed!r} not in ALLOWED_LITERAL_PHRASES")
    assert not violations, (
        "untranslated text found between tags (add $t(...) for it, or extend "
        "ALLOWED_LITERAL_PHRASES with a justification if RF8 exempts it):\n" + "\n".join(violations)
    )


def _balanced_call_args(code, open_paren_idx):
    """The substring strictly between the `(` at `open_paren_idx` and its matching `)`, tracking
    string/template-literal state so a `)` or nested `(` inside a literal (including `${...}`
    inside a backtick) never miscounts as the call's own parens."""
    depth = 0
    i = open_paren_idx
    in_str = None
    template_expr_depth = 0
    n = len(code)
    while i < n:
        c = code[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if in_str == "`" and c == "$" and i + 1 < n and code[i + 1] == "{":
                template_expr_depth += 1
                i += 2
                continue
            if in_str == "`" and template_expr_depth > 0:
                if c == "{":
                    template_expr_depth += 1
                elif c == "}":
                    template_expr_depth -= 1
                i += 1
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in "'\"`":
            in_str = c
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return code[open_paren_idx + 1 : i]
        i += 1
    raise ValueError("unbalanced parentheses")


def _untranslated_words_in_literal(raw_literal, extra_allowed=frozenset()):
    inner = INTERPOLATION_SPAN_RE.sub(" ", raw_literal[1:-1])
    words = set(WORD_RE.findall(inner))
    return words - ALLOWED_LITERAL_PHRASES_WORDS - extra_allowed


# The phrase whitelist above is for whole template chunks; a literal passed to notify()/etc. is
# free-form JS, not a stripped template chunk, so it's checked at the word level instead (a
# `'Failed'` fallback is one word and still has to be caught). Built once from the same source of
# truth so the two mechanisms cannot silently diverge.
ALLOWED_LITERAL_PHRASES_WORDS = {word for phrase in ALLOWED_LITERAL_PHRASES for word in WORD_RE.findall(phrase)}


def _script_without_templates_and_dict(text):
    script = _script_block(text).group(1)
    script = LANG_DICT_RE.sub(" ", script)
    script = TEMPLATE_LITERAL_RE.sub(" ", script)
    return script


def test_i18n_no_untranslated_text_in_script_sinks():
    """`notify()`/`confirm()`/`alert()`/`output.value = ...`/the log viewers' own connection
    messages compose interface text from plain JS, outside any `template:` string — invisible to
    the two tests above by construction. This is the scope gap that let 48 `notify()` calls, the
    only destructive `confirm()` in the GUI, and 3 WebSocket status messages stay in English under
    a Spanish UI (T4 review B1/B2). `r.error`/`r.stderr`/`e.message` are exempt by RF8 (they carry
    real system/backend output) and are never literals, so they never reach this check.

    A sink's arguments legitimately contain a *translation key* literal too — `notify(t('failed'))`
    — which is not display text and must not be flagged; `declared_keys` is how those are told
    apart from a stray hardcoded message.
    """
    text = _text()
    declared_keys = _dict_keys(_lang_dicts(text)["en"])
    script = _script_without_templates_and_dict(text)
    violations = []

    for match in SINK_CALL_RE.finditer(script):
        open_idx = match.end() - 1
        args = _balanced_call_args(script, open_idx)
        for lit in LITERAL_RE.finditer(args):
            raw = lit.group(0)
            if raw[1:-1] in declared_keys:
                continue
            bad = _untranslated_words_in_literal(raw, NOTIFY_TYPE_WORDS)
            if bad:
                line = script[: match.start()].count("\n") + 1
                violations.append(f"line {line}: {raw[:70]!r} (untranslated word(s): {sorted(bad)})")

    for match in OUTPUT_VALUE_RE.finditer(script):
        for lit in LITERAL_RE.finditer(match.group(1)):
            raw = lit.group(0)
            if raw[1:-1] in declared_keys:
                continue
            bad = _untranslated_words_in_literal(raw)
            if bad:
                line = script[: match.start()].count("\n") + 1
                violations.append(f"line {line}: output.value = {raw[:70]!r} (untranslated word(s): {sorted(bad)})")

    for match in GENERIC_RETURN_LITERAL_RE.finditer(script):
        raw = match.group(1)
        inner = INTERPOLATION_SPAN_RE.sub(" ", raw[1:-1])
        if " " not in inner:
            continue  # single-token enum return (`return 'running'`, `return 'badge-green'`...)
        bad = _untranslated_words_in_literal(raw)
        if bad:
            line = script[: match.start()].count("\n") + 1
            violations.append(f"line {line}: return {raw[:70]!r} (untranslated word(s): {sorted(bad)})")

    assert not violations, "untranslated text found outside template strings (route it through t(...)):\n" + "\n".join(
        violations
    )


def test_i18n_interpolation_placeholders_match_call_sites():
    """For every key with a `{var}` placeholder: `es` and `en` must declare the exact same set of
    placeholders, and every call site passing an interpolation object must pass exactly that set —
    nothing more, nothing less. Nothing else catches a `{count}`/`{total}` mismatch between the two
    languages, or a call site passing the wrong variable name: `t()` doesn't substitute a missing
    placeholder, it just leaves the literal `{count}` on screen (T4 review I3)."""
    text = _text()
    script = _script_block(text).group(1)
    dicts = _lang_dicts(text)
    es_entries = dict(DICT_ENTRY_RE.findall(dicts["es"]))
    en_entries = dict(DICT_ENTRY_RE.findall(dicts["en"]))

    call_site_vars = {}
    for match in TRANSLATION_CALL_WITH_VARS_RE.finditer(script):
        key, vars_blob = match.group(1), match.group(2)
        names = frozenset(re.findall(r"([a-zA-Z0-9_]+)\s*:", vars_blob))
        call_site_vars.setdefault(key, []).append(names)

    failures = []
    for key in sorted(set(es_entries) | set(en_entries) | set(call_site_vars)):
        es_placeholders = frozenset(INTERP_PLACEHOLDER_RE.findall(es_entries.get(key, "")))
        en_placeholders = frozenset(INTERP_PLACEHOLDER_RE.findall(en_entries.get(key, "")))
        if es_placeholders != en_placeholders:
            failures.append(f"{key}: es placeholders {sorted(es_placeholders)} != en placeholders {sorted(en_placeholders)}")
            continue
        call_sites = call_site_vars.get(key, [])
        if es_placeholders and not call_sites:
            failures.append(f"{key}: declares placeholders {sorted(es_placeholders)} but no call site passes vars")
        for names in call_sites:
            if names != es_placeholders:
                failures.append(f"{key}: call site passes {sorted(names)}, dict declares {sorted(es_placeholders)}")
    assert not failures, "\n".join(failures)
