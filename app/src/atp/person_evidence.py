"""Source-level professional identity, separate from the meeting company.

Only retrieved dedicated profiles can establish identity continuity. Search
titles, names in feeds and same-name URLs alone never establish a binding.
"""

import re
from urllib.parse import urlsplit


def normal(value):
    return " ".join(re.findall(r"[^\W_]+", str(value).casefold()))


def contains(text, value):
    needle = normal(value)
    return bool(needle) and f" {needle} " in f" {normal(text)} "


def company_core(name):
    return re.sub(r"\b(?:gmbh|ag|kg|co|se|ltd|inc)\b", " ", normal(name)).strip()


def url_key(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host.endswith(".linkedin.com"):
            host = "linkedin.com"
        return host, parsed.path.rstrip("/")
    except ValueError:
        return "", ""


def profile_url(url):
    host, path = url_key(url)
    return bool(
        (host == "linkedin.com" or host.endswith(".linkedin.com"))
        and re.fullmatch(r"/in/[^/]+", path)
        or re.fullmatch(r"(?:www\.)?freelancermap\.[a-z.]+", host)
        and re.fullmatch(r"/profil/[^/]+", path)
    )


def named_person_source(source, person):
    """An input selection hint; this alone is explicitly not identity proof."""
    if contains(source.title, person["name"]):
        return True
    slug = "-".join(normal(person["name"]).split())
    path = urlsplit(source.url).path.casefold()
    return bool(slug and re.search(r"/(?:in|posts)/" + re.escape(slug) + r"(?:[-_/]|$)", path))


def _profile_candidates(query, sources):
    """Bind the entire dedicated profile, not each job to the target company.

    Require the exact person name in title and retrieved body plus either the
    target organisation in that profile, or an explicitly supplied profile URL.
    Multiple same-name participants are never assigned automatically.
    """
    bindings = {}
    for person in query.people:
        if sum(normal(p["name"]) == normal(person["name"]) for p in query.people) != 1:
            continue
        for source in sources:
            if not source.verified or source.retrieval_kind != "direct" or not profile_url(source.url):
                continue
            if not contains(source.title, person["name"]) or not contains(source.excerpt, person["name"]):
                continue
            supplied = bool(person.get("profile_url")) and url_key(source.url) == url_key(
                person["profile_url"]
            )
            if not supplied and not contains(source.excerpt, company_core(query.company_name)):
                continue
            bindings.setdefault(person["participant_id"], []).append(source)
    return bindings


def profile_conflicts(query, sources):
    conflicts = set()
    for ref, profiles in _profile_candidates(query, sources).items():
        identities = {}
        for source in profiles:
            host, path = url_key(source.url)
            family = "freelancermap" if host.startswith("freelancermap.") else host
            identities.setdefault(family, set()).add(path)
        if any(len(paths) > 1 for paths in identities.values()):
            conflicts.add(ref)
    return conflicts


def profile_bindings(query, sources):
    conflicts = profile_conflicts(query, sources)
    return {ref: group for ref, group in _profile_candidates(query, sources).items() if ref not in conflicts}


def identity_excerpt(source, person, query, employer=""):
    """Keep verbatim identity context, not the entire profile on every citation."""
    passages = []
    for term in (person["name"], company_core(query.company_name), employer):
        words = normal(term).split()
        if not words:
            continue
        match = re.search(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", source.excerpt, re.I)
        if match:
            passages.append(source.excerpt[max(0, match.start() - 70) : match.end() + 180])
    return "\n[…]\n".join(dict.fromkeys(passages))


def extraction_sources(query, sources):
    """Protect named profiles from unrelated pages and repeated company material."""
    people = [s for s in sources if any(named_person_source(s, p) for p in query.people)]
    people.sort(key=lambda s: not profile_url(s.url))
    chosen, counts = [], {p["participant_id"]: 0 for p in query.people}
    for source in people:
        refs = [p["participant_id"] for p in query.people if named_person_source(source, p)]
        if any(counts[ref] < 2 for ref in refs) and len(chosen) < 8:
            chosen.append(source)
            for ref in refs:
                counts[ref] += 1
    domain = query.domain.lower().removeprefix("www.")
    company = [
        s
        for s in sources
        if s not in people
        and (
            (urlsplit(s.url).hostname or "").lower().removeprefix("www.") == domain
            or contains(s.excerpt, company_core(query.company_name))
        )
    ]
    company.sort(key=lambda s: (urlsplit(s.url).hostname or "").removeprefix("www.") != domain)
    # Legacy evidence adapters can omit titles or company names. Preserve a
    # bounded fallback when no relevant material has yet been established.
    if not chosen and not company:
        company = sources[:6]
    return chosen + company[: 6 if chosen else 12]


def extraction_excerpts(query, sources):
    from .passages import source_passages

    personal = [s for s in sources if any(named_person_source(s, p) for p in query.people)]
    company_count = len(sources) - len(personal)
    personal_limit = min(12000, (64000 - company_count * 1800) // max(1, len(personal)))
    company_limit = min(10000, (64000 - personal_limit * len(personal)) // max(1, company_count))
    return {
        s.source_id: s.excerpt
        if s in personal and len(s.excerpt) <= personal_limit
        else source_passages(s, query, personal_limit if s in personal else company_limit)
        for s in sources
    }
