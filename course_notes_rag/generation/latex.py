"""Light LaTeX validation for text that KaTeX will render on the frontend."""

from __future__ import annotations

import re

_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")
_LEFT_RIGHT = re.compile(r"\\(left|right)(?![A-Za-z])")


def math_segments(text: str) -> tuple[list[str], list[str]]:
    """Extract ``$$display$$`` and ``$inline$`` segments; returns (segments, errors)."""
    segments: list[str] = []
    errors: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] == "$":
            i += 2
            continue
        if ch != "$":
            i += 1
            continue
        display = text.startswith("$$", i)
        delim = "$$" if display else "$"
        start = i + len(delim)
        j = start
        while True:
            j = text.find(delim, j)
            if j < 0:
                errors.append(f"Unclosed {delim} math delimiter")
                return segments, errors
            if text[j - 1] == "\\":
                j += 1
                continue
            break
        body = text[start:j]
        if not display and "\n\n" in body:
            errors.append("Inline math spans a paragraph break")
        if not body.strip():
            errors.append(f"Empty {delim}{delim} math segment")
        segments.append(body)
        i = j + len(delim)
    return segments, errors


def validate_latex(text: str) -> list[str]:
    """Return a list of problems (empty when the math in ``text`` looks renderable)."""
    segments, errors = math_segments(text)
    if any(ch in text for ch in "\x08\x0c") or re.search(r"\t[A-Za-z]", text):
        errors.append("Control character in text (a LaTeX backslash was read as a JSON escape)")
    for body in segments:
        depth = 0
        k = 0
        while k < len(body):
            c = body[k]
            if c == "\\" and k + 1 < len(body):
                k += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth < 0:
                    break
            k += 1
        if depth != 0:
            errors.append(f"Unbalanced braces in math: {body[:60]!r}")
        envs: list[str] = []
        for kind, name in _ENV.findall(body):
            if kind == "begin":
                envs.append(name)
            elif not envs or envs.pop() != name:
                errors.append(f"Mismatched \\end{{{name}}}")
        if envs:
            errors.append(f"Unclosed \\begin{{{envs[-1]}}}")
        lr = _LEFT_RIGHT.findall(body)
        if lr.count("left") != lr.count("right"):
            errors.append("Unbalanced \\left / \\right")
    return errors
