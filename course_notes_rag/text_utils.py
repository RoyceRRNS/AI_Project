"""Sentence splitting and token counting shared by the chunker and the generators."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from functools import lru_cache

TokenCounter = Callable[[str], int]

# Inline math and bracketed citations such as "[Source: a.pdf, p. 2]" are never split.
_INLINE_MATH = re.compile(r"\$[^$\n]+?\$|\[[^\[\]\n]{1,300}\]")
_ABBREVIATIONS = (
    "e.g.", "i.e.", "etc.", "vs.", "Fig.", "fig.", "Eq.", "eq.", "Eqs.", "Sec.", "sec.",
    "Thm.", "Def.", "Prop.", "Lem.", "Cor.", "Dr.", "Prof.", "Mr.", "Ms.", "No.", "approx.", "cf.",
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9\\$])")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking inline ``$...$`` math or abbreviations."""
    text = text.strip()
    if not text:
        return []
    placeholders: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = match.group(0)
        return key

    masked = _INLINE_MATH.sub(protect, text)
    for abbr in _ABBREVIATIONS:
        if abbr in masked:
            key = f"\x00{len(placeholders)}\x00"
            placeholders[key] = abbr
            masked = masked.replace(abbr, key)
    pieces = [p.strip() for p in _SENTENCE_END.split(masked) if p.strip()]

    def restore(piece: str) -> str:
        # Placeholders may nest (an abbreviation inside restored math is impossible, but be safe).
        for _ in range(3):
            if "\x00" not in piece:
                break
            for key, value in placeholders.items():
                piece = piece.replace(key, value)
        return piece

    return [restore(p) for p in pieces]


_APPROX_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def approx_token_count(text: str) -> int:
    """Cheap tokenizer-free estimate: one token per word or symbol."""
    return len(_APPROX_TOKEN.findall(text))


@lru_cache(maxsize=4)
def _hf_tokenizer(model_name: str):  # pragma: no cover - exercised by live tests
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_name)


def hf_token_counter(model_name: str) -> TokenCounter:  # pragma: no cover - live only
    tokenizer = _hf_tokenizer(model_name)

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    return count


def tail_words(text: str, max_tokens: int, count: TokenCounter) -> str:
    """Return the longest suffix of ``text`` (on word boundaries) within ``max_tokens``."""
    if max_tokens <= 0 or not text:
        return ""
    words = text.split()
    lo, hi = 0, len(words)
    # Binary search for the smallest start index whose suffix fits.
    while lo < hi:
        mid = (lo + hi) // 2
        if count(" ".join(words[mid:])) <= max_tokens:
            hi = mid
        else:
            lo = mid + 1
    return " ".join(words[lo:])


def linearize_table(markdown: str) -> str:
    """Rewrite a GFM table as one "Header: value; ..." sentence per row.

    Embedders and cross-encoders match a question like "variance of a Bernoulli variable"
    far better against "Distribution: Bernoulli; ...; Variance: p(1 - p)." than against pipes.
    """
    rows = [r.strip() for r in markdown.splitlines() if r.strip().startswith("|")]
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(set(c) <= set("-: ") for c in r)]
    if len(cells) < 2:
        return markdown
    header, body = cells[0], cells[1:]
    lines = []
    for row in body:
        pairs = [f"{h}: {v}" if h else v for h, v in zip(header, row) if v]
        if pairs:
            lines.append("; ".join(pairs) + ".")
    return "\n".join(lines) or markdown


def linearize_tables_in(text: str) -> str:
    """Apply :func:`linearize_table` to every table paragraph inside Markdown text."""
    paragraphs = text.split("\n\n")
    for i, para in enumerate(paragraphs):
        lines = [ln for ln in para.strip().splitlines() if ln.strip()]
        if len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines):
            paragraphs[i] = linearize_table(para)
    return "\n\n".join(paragraphs)


_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "−": "-", " ": " ",
})


def normalize_for_match(text: str) -> tuple[str, list[int]]:
    """Lower-case, unify quotes/dashes and collapse whitespace.

    Returns the normalized string plus, for each of its characters, the index of the
    original character it came from, so a match can be mapped back to the source text.
    """
    out: list[str] = []
    index: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        ch = unicodedata.normalize("NFKC", ch).translate(_QUOTE_MAP)
        for sub in ch:
            if sub == "*":
                continue
            if sub.isspace():
                if prev_space:
                    continue
                out.append(" ")
                index.append(i)
                prev_space = True
            else:
                out.append(sub.lower())
                index.append(i)
                prev_space = False
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def find_verbatim(quote: str, text: str) -> tuple[int, int] | None:
    """Locate ``quote`` inside ``text`` ignoring case, whitespace and quote styles.

    Returns the ``(start, end)`` character span in the *original* ``text``.
    """
    norm_quote, _ = normalize_for_match(quote.strip().strip("\"'"))
    if not norm_quote:
        return None
    norm_text, index = normalize_for_match(text)
    pos = norm_text.find(norm_quote)
    if pos < 0:
        return None
    start = index[pos]
    end = index[pos + len(norm_quote) - 1] + 1
    return start, end
