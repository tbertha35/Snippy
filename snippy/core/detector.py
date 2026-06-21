"""Content-type detector for Snippy.

Given a string, decide which of the supported types it most likely is:
`url`, `email`, `phone`, `code`, `path`, or `text`.

Detection is intentionally simple (no ML, no LLM). The goal is just to
pick a sensible icon and color for the snippet in the palette.

Phase 1 keeps it as pure functions so they can be unit-tested without
spinning up Qt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# The set of content types Snippy understands in Phase 1.
TYPE_URL = "url"
TYPE_EMAIL = "email"
TYPE_PHONE = "phone"
TYPE_CODE = "code"
TYPE_PATH = "path"
TYPE_TEXT = "text"

ALL_TYPES: tuple[str, ...] = (TYPE_URL, TYPE_EMAIL, TYPE_PHONE, TYPE_CODE, TYPE_PATH, TYPE_TEXT)


# Color hint associated with each type (used for toast + tray icon flash in
# the feedback layer). Phase 1 uses simple hex; Phase 2 will source these
# from the active theme.
TYPE_COLORS: dict[str, str] = {
    TYPE_URL: "#3b82f6",     # blue
    TYPE_EMAIL: "#10b981",   # green
    TYPE_PHONE: "#a855f7",   # purple
    TYPE_CODE: "#f59e0b",    # amber
    TYPE_PATH: "#64748b",    # slate
    TYPE_TEXT: "#94a3b8",    # cool gray
}


# Icon glyph (emoji) for each type. Phase 1 keeps it simple; the visual
# icons get refined in the styles module.
TYPE_ICONS: dict[str, str] = {
    TYPE_URL: "🔗",
    TYPE_EMAIL: "✉️",
    TYPE_PHONE: "📞",
    TYPE_CODE: "⟨⟩",
    TYPE_PATH: "📁",
    TYPE_TEXT: "📝",
}


@dataclass(slots=True, frozen=True)
class DetectionResult:
    type: str
    confidence: float  # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# URL — conservative; matches http(s) and common schemes. We're not trying
# to be a URL parser, just to recognize one.
_RE_URL = re.compile(
    r"""
    \b
    (?P<scheme>https?|ftp|file)://
    [^\s<>"]+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Email — RFC-5322 is a beast. This is "good enough" for icon purposes.
_RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Phone — matches things that look like phone numbers in international or
# US formats. Loosely tuned; false positives are tolerable (we just show
# a phone icon and store it as a phone-type snippet).
_RE_PHONE = re.compile(
    r"""
    (?<!\d)                              # not in the middle of a longer number
    (?:\+?\d{1,3}[\s.\-]?)?
    (?:\(?\d{2,4}\)?[\s.\-]?){2,4}
    \d{3,4}
    (?!\d)
    """,
    re.VERBOSE,
)

# Filesystem paths — Windows drive letter or Unix absolute path.
_RE_PATH_WIN = re.compile(r"^[A-Za-z]:[\\/](?:[^<>\"|?*\n\r]+)$")
_RE_PATH_UNIX = re.compile(r"^(/Users/|/home/|/etc/|/var/|/tmp/|/opt/|~/)[\w./\-]+$")

# Heuristics for code — line breaks + at least 2 of these signals.
_CODE_SIGNALS = (
    "    ",  # 4-space indent
    "\t",   # tab
    "()",   # parens
    "{}",   # braces
    "[]",   # brackets
    "=>",   # arrow
    "==",   # equality
    "!=",   # inequality
    "&&",
    "||",
    "def ",
    "class ",
    "function ",
    "import ",
    "from ",
    "return ",
    "if (",
    "for (",
    "while (",
    "#!/",
)


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def _looks_like_code(content: str) -> bool:
    if "\n" not in content:
        return False
    score = 0
    for signal in _CODE_SIGNALS:
        if signal in content:
            score += 1
    return score >= 2


def _looks_like_path(content: str) -> bool:
    if len(content) > 4096 or "\n" in content:
        return False
    if _RE_PATH_WIN.match(content) or _RE_PATH_UNIX.match(content):
        return True
    return False


def detect(content: str) -> DetectionResult:
    """Classify `content` and return the type + confidence.

    Order matters: more specific checks (URL, email) run before looser ones
    (phone, code, path). The fallback is `text` with confidence 1.0.
    """
    if not content or not content.strip():
        return DetectionResult(TYPE_TEXT, 1.0)

    stripped = content.strip()

    if _RE_URL.search(stripped):
        return DetectionResult(TYPE_URL, 0.95)

    if _RE_EMAIL.search(stripped):
        return DetectionResult(TYPE_EMAIL, 0.9)

    if _looks_like_path(stripped):
        return DetectionResult(TYPE_PATH, 0.85)

    # Phone — strip whitespace and check digit count
    if _RE_PHONE.search(stripped):
        digits = re.sub(r"\D", "", stripped)
        if 7 <= len(digits) <= 15:
            return DetectionResult(TYPE_PHONE, 0.7)

    if _looks_like_code(stripped):
        return DetectionResult(TYPE_CODE, 0.6)

    return DetectionResult(TYPE_TEXT, 1.0)


def icon_for(type_: str) -> str:
    return TYPE_ICONS.get(type_, TYPE_ICONS[TYPE_TEXT])


def color_for(type_: str) -> str:
    return TYPE_COLORS.get(type_, TYPE_COLORS[TYPE_TEXT])