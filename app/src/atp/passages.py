"""Bounded verbatim passage selection, including material late in a document."""

import re

DEFAULT_TERMS = (
    "geschäftsmodell",
    "produkte",
    "leistungen",
    "kunden",
    "märkte",
    "mitarbeit",
    "beschäftigt",
    "employees",
    "products",
    "services",
    "markets",
    "family",
    "familien",
    "eigentümer",
    "inhaber",
    "management",
    "geschäftsführ",
    "vorstand",
    "projekt",
    "project",
    "digital",
    "artificial intelligence",
    "künstliche intelligenz",
    "ki-",
    "ai-",
    "2025",
    "2026",
)


def select_passages(text, terms=(), limit=12000):
    """Keep original passage substrings: no model paraphrases or synthetic quotes."""
    if len(text) <= limit:
        return text
    weighted = []
    explicit = tuple(t.casefold() for t in terms if len(t) >= 3)
    all_terms = tuple(dict.fromkeys((*explicit, *DEFAULT_TERMS)))
    lowered = text.casefold()
    # Bounded scanning of a maximum 2 MiB HTML / bounded PDF text document.
    for index, term in enumerate(all_terms):
        position, occurrences = 0, 0
        while occurrences < 12:
            found = lowered.find(term, position)
            if found < 0:
                break
            start, end = max(0, found - 250), min(len(text), found + 850)
            fragment = lowered[start:end]
            score = (5 if index < len(explicit) else 1) + sum(t in fragment for t in all_terms)
            weighted.append((score, start, end))
            position, occurrences = found + len(term), occurrences + 1
    selected, remaining = [(0, min(1800, limit))], limit - min(1800, limit) - 7
    for _score, start, end in sorted(weighted, key=lambda item: (-item[0], -item[1])):
        if remaining < 200:
            break
        if any(start < previous_end and end > previous_start for previous_start, previous_end in selected):
            continue
        end = min(end, start + remaining)
        selected.append((start, end))
        remaining -= end - start + 7
    if len(selected) == 1 and remaining > 200:
        selected.append((max(selected[0][1], len(text) - remaining), len(text)))
    return " […] ".join(text[start:end] for start, end in sorted(selected))[:limit]


def source_passages(source, query, limit=10000):
    terms = [query.company_name, *re.findall(r"\w{3,}", query.company_name)]
    terms.extend(p.get("name", "") for p in query.people)
    return select_passages(source.excerpt, terms, limit)
