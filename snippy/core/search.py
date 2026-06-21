"""Fuzzy search for Snippy.

Phase 1: in-memory scoring using `rapidfuzz` (WRatio + token-set + partial).
Phase 2:
    - **WS3: Frecency** — blend fuzzy score with recency + frequency.
    - **WS6: Tag filtering** — filter by tags the snippet has / doesn't have.
    - **WS7: Search operators** — parse `tag:work type:url pin:yes before:2026-01-01`
      out of the query and apply them as pre-filters. The remaining free
      text still gets fuzzy-matched.

Search-operator cheat sheet (also in README + ROADMAP):
    tag:foo     → only snippets tagged with "foo"
    type:url    → only snippets of this content type
    pin:yes|no  → only pinned or only unpinned
    before:YYYY-MM-DD
    after:YYYY-MM-DD
    archive:yes|no  → include/exclude archived
    free text   → fuzzy match against content (Phase 1 behavior)

Phase 5+ will swap to SQLite FTS5 for very large libraries (>5k snippets).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from rapidfuzz import fuzz, utils

from snippy.core.db import Snippet


LOGGER = logging.getLogger(__name__)


# Default score threshold — below this, a snippet is hidden.
DEFAULT_MIN_SCORE = 55.0

# Frecency weights (must sum to 1.0)
_FRECENCY_FUZZY = 0.7
_FRECENCY_RECENCY = 0.2
_FRECENCY_FREQUENCY = 0.1


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SearchHit:
    snippet: Snippet
    score: float
    highlight_ranges: list[tuple[int, int]] = field(default_factory=list)

    @property
    def preview(self) -> str:
        """A short single-line preview of the content for the result list."""
        first = self.snippet.content.splitlines()[0] if self.snippet.content else ""
        if len(first) > 120:
            return first[:117] + "…"
        return first


# ---------------------------------------------------------------------------
# Query parsing (WS7)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ParsedQuery:
    """A user query split into its operator filters + the residual free text."""
    free_text: str
    tag: str | None = None
    type: str | None = None
    pin: bool | None = None          # True = pinned only, False = unpinned only
    archive: bool | None = None      # True = archived only, False = non-archived
    before: datetime | None = None
    after: datetime | None = None

    def has_filters(self) -> bool:
        return any(
            v is not None
            for v in (self.tag, self.type, self.pin, self.archive, self.before, self.after)
        )

    def describe(self) -> str:
        """Human-readable summary of active filters (for the palette UI)."""
        bits: list[str] = []
        if self.tag:
            bits.append(f"tag: {self.tag}")
        if self.type:
            bits.append(f"type: {self.type}")
        if self.pin is not None:
            bits.append("pinned" if self.pin else "unpinned")
        if self.archive is True:
            bits.append("archived")
        if self.before:
            bits.append(f"before {self.before.date()}")
        if self.after:
            bits.append(f"after {self.after.date()}")
        return " · ".join(bits)


_OPERATOR_RE = re.compile(r"\b(tag|type|pin|archive|before|after):(\S+)", re.IGNORECASE)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


def _parse_date(s: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_query(query: str) -> ParsedQuery:
    """Extract `tag:`, `type:`, `pin:`, etc. operators from the query string.

    Anything that isn't an operator becomes the `free_text` portion and gets
    fuzzy-matched. Whitespace separates tokens but operators are matched
    anywhere in the string (so `tag:work github` works).
    """
    tag = type_ = None
    pin = archive = None
    before = after = None

    matches = list(_OPERATOR_RE.finditer(query))
    for m in matches:
        key = m.group(1).lower()
        value = m.group(2)
        if key == "tag":
            tag = value
        elif key == "type":
            type_ = value.lower()
        elif key == "pin":
            v = value.lower()
            if v in ("yes", "true", "1", "pinned"):
                pin = True
            elif v in ("no", "false", "0", "unpinned"):
                pin = False
        elif key == "archive":
            v = value.lower()
            if v in ("yes", "true", "1"):
                archive = True
            elif v in ("no", "false", "0"):
                archive = False
        elif key == "before":
            before = _parse_date(value)
        elif key == "after":
            after = _parse_date(value)

    # Remove operators (and any leftover whitespace) from the free text.
    # Iterating in reverse so .span() offsets stay valid as we splice.
    cleaned = query
    for m in reversed(matches):
        cleaned = cleaned[: m.start()] + " " + cleaned[m.end():]
    free_text = re.sub(r"\s+", " ", cleaned).strip()

    return ParsedQuery(
        free_text=free_text,
        tag=tag,
        type=type_,
        pin=pin,
        archive=archive,
        before=before,
        after=after,
    )


# ---------------------------------------------------------------------------
# Filtering (WS6/WS7 pre-filters, applied before fuzzy scoring)
# ---------------------------------------------------------------------------


def _snippet_has_tag(snippet: Snippet, tag: str) -> bool:
    """Best-effort tag check using the in-memory representation.

    `Snippet` itself doesn't carry tags yet (they live in the join table).
    The `search()` function takes an optional `tags_by_snippet` mapping
    that the caller (app.py) populates from `Database.get_tags_for_snippet`.
    """
    return False  # default; overridden by _apply_filters using the mapping


def _apply_filters(
    snippets: Iterable[Snippet],
    parsed: ParsedQuery,
    tags_by_snippet: dict[int, set[str]] | None = None,
) -> list[Snippet]:
    out: list[Snippet] = []
    for snip in snippets:
        if parsed.tag:
            tags = (tags_by_snippet or {}).get(snip.id, set())
            if parsed.tag.lower() not in {t.lower() for t in tags}:
                continue
        if parsed.type and snip.content_type.lower() != parsed.type:
            continue
        if parsed.pin is True and not snip.is_pinned:
            continue
        if parsed.pin is False and snip.is_pinned:
            continue
        if parsed.archive is True and not snip.is_archived:
            continue
        if parsed.archive is False and snip.is_archived:
            continue
        if parsed.before:
            created = _parse_iso(snip.created_at)
            if created is None or created >= parsed.before:
                continue
        if parsed.after:
            created = _parse_iso(snip.created_at)
            if created is None or created <= parsed.after:
                continue
        out.append(snip)
    return out


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Scoring (Phase 1) + Frecency (Phase 2 WS3)
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return utils.default_process(s)


def _fuzzy_score(query: str, snippet: Snippet) -> float:
    """The pure-fuzzy part of the score, in [0, 100]."""
    if not query:
        return 50.0  # neutral for empty query

    q_norm = _normalize(query)
    c_norm = _normalize(snippet.content)

    ratio = fuzz.WRatio(q_norm, c_norm, score_cutoff=0)
    token_set = fuzz.token_set_ratio(q_norm, c_norm, score_cutoff=0)
    partial = fuzz.partial_ratio(q_norm, c_norm, score_cutoff=0)

    return (0.5 * ratio) + (0.3 * token_set) + (0.2 * partial)


def _recency_score(snippet: Snippet, *, now: datetime | None = None) -> float:
    """0..100 — higher for newer snippets. Uses a half-life of 30 days."""
    now = now or datetime.now(timezone.utc)
    last = _parse_iso(snippet.last_used_at or snippet.created_at)
    if last is None:
        return 0.0
    age_days = max(0.0, (now - last).total_seconds() / 86400.0)
    # Exponential decay: 100 at day 0, 50 at day 30, ~0 at day 180
    import math
    return 100.0 * math.exp(-age_days / 30.0)


def _frequency_score(snippet: Snippet) -> float:
    """0..100 — logarithmic so power-users don't drown out casual ones."""
    n = max(0, snippet.use_count)
    if n == 0:
        return 0.0
    # log10(n+1) / log10(101) maps:
    #   n=0    → 0
    #   n=1    → ~15
    #   n=10   → ~50
    #   n=100  → 100
    import math
    return 100.0 * (math.log10(n + 1) / math.log10(101))


def _score_one(
    query: str,
    snippet: Snippet,
    *,
    use_frecency: bool = True,
    now: datetime | None = None,
) -> SearchHit:
    fuzzy = _fuzzy_score(query, snippet)
    if use_frecency and query:
        rec = _recency_score(snippet, now=now)
        freq = _frequency_score(snippet)
        score = (
            _FRECENCY_FUZZY * fuzzy
            + _FRECENCY_RECENCY * rec
            + _FRECENCY_FREQUENCY * freq
        )
    else:
        score = fuzzy
    # Small pin boost
    if snippet.is_pinned:
        score += 5.0

    # Highlights (substring matches in raw content)
    ranges: list[tuple[int, int]] = []
    if query:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        for m in pattern.finditer(snippet.content):
            ranges.append((m.start(), m.end()))
            if len(ranges) >= 8:
                break

    return SearchHit(snippet=snippet, score=float(score), highlight_ranges=ranges)


# ---------------------------------------------------------------------------
# Public search entry point
# ---------------------------------------------------------------------------


def search(
    query: str,
    snippets: Iterable[Snippet],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    limit: int = 50,
    use_frecency: bool = True,
    tags_by_snippet: dict[int, set[str]] | None = None,
    now: datetime | None = None,
) -> list[SearchHit]:
    """Return snippets matching `query`, sorted by descending score.

    `query` may contain operators (see module docstring). Pre-filters apply
    before fuzzy scoring; free text still gets fuzzy-matched.
    """
    parsed = parse_query(query)
    filtered = _apply_filters(snippets, parsed, tags_by_snippet=tags_by_snippet)

    if not parsed.free_text:
        # Empty free text → recents, pinned first
        ordered = sorted(
            filtered,
            key=lambda s: (
                not s.is_pinned,
                s.last_used_at or s.created_at,
            ),
            reverse=True,
        )
        return [SearchHit(snippet=s, score=50.0) for s in ordered[:limit]]

    hits: list[SearchHit] = []
    for snip in filtered:
        hit = _score_one(parsed.free_text, snip, use_frecency=use_frecency, now=now)
        if hit.score >= min_score:
            hits.append(hit)

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]