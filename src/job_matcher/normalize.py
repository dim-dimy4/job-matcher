"""Source-agnostic helpers that turn raw strings into `Vacancy` field values."""

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_matcher.models import Language, Seniority


def clean_url(url: str) -> str:
    """Drop tracking params (utm_*) and the fragment so one posting has one URL (dedup key)."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def html_to_text(markup: str) -> str:
    """Rough HTML -> plain text, good enough for keyword and language checks."""
    text = html.unescape(_TAG.sub(" ", markup))
    return _SPACES.sub(" ", text).strip()


# Checked from the highest grade down: "Middle/Senior" is senior, "Junior+ / Middle" is middle.
_SENIORITY_PATTERNS = [
    (Seniority.LEAD, re.compile(r"\b(lead|head|principal)\b", re.IGNORECASE)),
    (Seniority.SENIOR, re.compile(r"\b(senior|sr|expert)\b", re.IGNORECASE)),
    (Seniority.MIDDLE, re.compile(r"\b(middle|mid)\b", re.IGNORECASE)),
    (Seniority.JUNIOR, re.compile(r"\b(junior|jr|intern|trainee)\b", re.IGNORECASE)),
]


def detect_seniority(title: str) -> Seniority | None:
    for seniority, pattern in _SENIORITY_PATTERNS:
        if pattern.search(title):
            return seniority
    return None


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[^\W\d_]+")
# Frequent function words. Counting them beats counting letters: one "Wrocław" in an
# English post adds Polish letters, but not Polish grammar.
_EN_WORDS = frozenset(
    {"the", "and", "with", "for", "to", "of", "in", "you", "we", "our", "is", "are", "be", "on"}
)
_PL_WORDS = frozenset(
    {"w", "z", "na", "do", "się", "jest", "oraz", "dla", "nie", "że", "od", "po", "lub", "są"}
)


def detect_language(text: str) -> Language | None:
    """Heuristic: mostly Cyrillic -> uk; otherwise more Polish than English function words -> pl."""
    letters = sum(c.isalpha() for c in text)
    if not letters:
        return None
    if len(_CYRILLIC.findall(text)) / letters > 0.3:
        return Language.UK
    words = [w.lower() for w in _WORD.findall(text)]
    pl = sum(w in _PL_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    return Language.PL if pl > en else Language.EN


# SPEC §6: "Polish C1 / fluent Polish / język polski".
_POLISH_REQUIRED = re.compile(
    r"polish\W{0,3}(?:at\W{1,3})?(?:c1|c2|native|fluent)"
    r"|(?:fluent|native|proficient|excellent)\W{1,3}(?:in\W{1,3})?polish"
    r"|język\w*\s+polsk",
    re.IGNORECASE,
)


def requires_polish(text: str, language: Language | None) -> bool:
    return language is Language.PL or bool(_POLISH_REQUIRED.search(text))
