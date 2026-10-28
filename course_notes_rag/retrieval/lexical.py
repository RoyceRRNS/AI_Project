"""Math-aware tokenizer for BM25.

Keeps what generic tokenizers throw away in lecture notes: LaTeX commands, Greek
letters (Unicode and ``\\alpha`` spellings map to the same token), math symbols,
sub/superscripted variables (``x_i``, ``w^T``) and dotted theorem numbers (``3.2``).
"""

from __future__ import annotations

import re

GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda",
    "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma",
    "τ": "tau", "υ": "upsilon", "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "gamma", "Δ": "delta", "Θ": "theta", "Λ": "lambda", "Ξ": "xi", "Π": "pi",
    "Σ": "sigma", "Φ": "phi", "Ψ": "psi", "Ω": "omega",
}

SYMBOLS = {
    "∇": "nabla", "∑": "sum", "∏": "prod", "∫": "int", "∂": "partial", "≤": "leq", "≥": "geq",
    "≠": "neq", "≈": "approx", "∞": "infty", "→": "to", "⇒": "implies", "⇔": "iff",
    "∈": "in", "∉": "notin", "⊂": "subset", "⊆": "subseteq", "∀": "forall", "∃": "exists",
    "±": "pm", "×": "times", "√": "sqrt", "‖": "norm", "⊤": "top", "∘": "circ", "⊙": "odot",
}

# LaTeX spellings that should match their Unicode counterparts.
LATEX_ALIASES = {"varepsilon": "epsilon", "varphi": "phi", "le": "leq", "ge": "geq", "ne": "neq",
                 "rightarrow": "to", "vert": "norm", "lvert": "norm", "rvert": "norm"}

STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does for from had has have how
    i if in into is it its may might of on or our so such that the their them then there these
    they this those to was we were what when where which while who why will with would you your
    also than using use used each other any all not no via""".split()
)

_TOKEN = re.compile(
    r"\\[A-Za-z]+"                     # LaTeX command
    r"|\d+(?:\.\d+)+"                  # dotted numbers: 3.2, 1.4.1
    r"|[A-Za-z]+(?:[_^]\{?[A-Za-z0-9]+\}?)+"  # x_i, w^T, h_{t}
    r"|[A-Za-z]+(?:'[A-Za-z]+)?"        # words
    r"|\d+"
    r"|[" + re.escape("".join(SYMBOLS)) + r"]"
)


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    for ch, name in GREEK.items():
        if ch in text:
            text = text.replace(ch, f" {name} ")
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        tok = match.group(0)
        if tok in SYMBOLS:
            tokens.append(SYMBOLS[tok])
            continue
        if tok.startswith("\\"):
            name = tok[1:].lower()
            tokens.append(LATEX_ALIASES.get(name, name))
            continue
        tok = tok.lower().replace("{", "").replace("}", "")
        if "_" in tok or "^" in tok:
            tokens.append(tok)
            base = re.split(r"[_^]", tok, maxsplit=1)[0]
            if base and base not in STOPWORDS:
                tokens.append(base)
            continue
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok in STOPWORDS:
            continue
        tokens.append(tok)
    return tokens
