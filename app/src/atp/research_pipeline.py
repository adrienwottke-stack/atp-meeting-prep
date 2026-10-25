"""Checkpointed public research waves, independent of the provider transport."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from urllib.parse import urlsplit

from .budget import BudgetExceeded, LeaseLost
from .evidence import EvidenceError
from .models import ResearchBundle, ResearchCoverage, ResearchQuery, Source
from .person_evidence import company_core, contains, named_person_source, profile_bindings


def research_needs(query, bundle):
    known = [f for f in bundle.facts if f.status == "verified"]
    company = {f.field for f in known if f.subject_ref == "company"}
    result = []
    definitions = [
        ("company", {"business_model", "services"}),
        ("developments", {"dated_business_development_or_ai_project"}),
        ("icp", {"employees_total", "owner_or_family_led", "is_corporation", "is_public_authority"}),
    ]
    for area, fields in definitions:
        present = company.copy()
        if area == "icp" and (
            any(f.field in ("owner_led", "family_led") and f.value is True for f in known)
            or all(
                any(f.field == name and f.value is False for f in known)
                for name in ("owner_led", "family_led")
            )
        ):
            present.add("owner_or_family_led")
        if area == "developments" and any(
            f.subject_ref == "company"
            and f.field in ("business_development", "ai_project")
            and f.status in ("verified", "historical")
            and f.information_as_of
            for f in bundle.facts
        ):
            present.add("dated_business_development_or_ai_project")
        missing = sorted(fields - present)
        result.append(
            ResearchCoverage(
                area=area,
                status="covered" if not missing else "partial" if fields & present else "missing",
                reasons=missing,
            )
        )
    people_missing = []
    for person in query.people:
        fields = {
            f.field
            for f in bundle.facts
            if f.subject_ref == person["participant_id"] and f.status in ("verified", "historical")
        }
        if not fields & {"current_role", "undated_role"}:
            people_missing.append(f"{person['participant_id']}:role_context")
        if not fields & {"career", "historical_role"}:
            people_missing.append(f"{person['participant_id']}:career")
        if "professional_topic" not in fields:
            people_missing.append(f"{person['participant_id']}:professional_topic")
    result.append(
        ResearchCoverage(
            area="people",
            status="covered"
            if not people_missing
            else "partial"
            if any(f.subject_ref != "company" for f in known)
            else "missing",
            reasons=people_missing,
        )
    )
    return result


def _identity(fact):
    return (
        fact.subject_ref,
        fact.field,
        json.dumps(fact.value, sort_keys=True, ensure_ascii=False),
        fact.information_as_of,
    )


def _preserve_ids(bundle, previous):
    old = {_identity(f): f.fact_id for f in previous.facts}
    unique = {}
    for fact in bundle.facts:
        key = _identity(fact)
        if key in unique:
            unique[key].evidence_ids = list(dict.fromkeys([*unique[key].evidence_ids, *fact.evidence_ids]))
        else:
            fact.fact_id = old.get(key, fact.fact_id)
            unique[key] = fact
    bundle.facts = list(unique.values())
    # Questions were built before stable IDs were assigned; regenerate options.
    for question in bundle.clarifications:
        if question.code == "K03":
            question.options = [
                f"Fakt {f.fact_id} bestätigen: {f.value} ({f.information_as_of or 'Zeitbezug offen'})"
                for f in bundle.facts
                if f.subject_ref == question.participant_id
                and f.field in ("current_role", "historical_role", "undated_role")
            ]
            question.options += ["Angabe korrigieren", "Als ungeklärt überspringen"]
    return bundle


def _research_seconds(budget):
    settings = getattr(budget, "settings", None)
    cutoff = getattr(settings, "research_cutoff_seconds", 390)
    elapsed = getattr(budget, "elapsed", max(0, 480 - budget.remaining_seconds))
    return max(0, min(budget.remaining_seconds - 90, cutoff - elapsed))


def public_search_plan(query, *, people_focus=False, posts_focus=False, missing_areas=None):
    """Concrete public queries; no meeting goal, private context or model knowledge."""
    company = f'"{query.company_name}" "{query.domain}" Leistungen Unternehmen'
    people = []
    for person in query.people:
        name = person["name"].replace('"', " ").strip()
        people.extend(
            [
                f'"{name}" "{query.company_name}"',
                f'"{name}" site:linkedin.com/posts/' if posts_focus else f'"{name}" site:linkedin.com/in/',
                f'"{name}" beruflicher Werdegang Fachbeiträge'
                if not person.get("role_hint")
                else f'"{name}" "{person["role_hint"]}"',
            ]
        )
    # The provider has a hard four-tool-call cap per request. Give every person
    # one exact-name query before optional profile/topic variants.
    primary = 1 if people_focus else 0
    first = [people[i] for i in range(primary, len(people), 3)]
    rest = [q for i, q in enumerate(people) if i % 3 != primary]
    targeted = []
    site_query = company
    for area in missing_areas or []:
        terms = {
            "company": "Leistungen Produkte Kunden Märkte",
            "developments": "Digitalisierung KI Projekte Neuigkeiten Geschäftsbericht",
            "icp": "Mitarbeiter Beschäftigte Geschäftsführung Eigentümer Konzern",
        }.get(area)
        if terms:
            targeted.append(f'"{query.company_name}" {terms}')
            if len(targeted) == 1:
                site_query = f'site:{query.domain} {terms}'
    company = site_query
    return (first + rest + targeted + [company] if people_focus else [company] + targeted + first + rest)[:4]


def evidence_queue(urls, query, *, initial=False, people_focus=False, titles=None):
    from .providers import _fetch_key, _rank_urls

    ranked = _rank_urls(urls, query.domain)
    if titles is not None:
        filtered, unknown, external_company = [], 0, 0
        seen = set()
        domain = query.domain.lower().removeprefix("www.")
        for url in ranked:
            key = _fetch_key(url)
            if key in seen:
                continue
            seen.add(key)
            host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            title = titles.get(url, "")
            try:
                candidate = Source(url=url, title=title)
            except ValueError:
                # Citation metadata is still untrusted. An invalid URL must
                # neither abort the whole wave nor enter the fetch queue.
                continue
            if (
                host == domain
                or host.endswith("." + domain)
                or any(named_person_source(candidate, person) for person in query.people)
            ):
                filtered.append(url)
            elif title and contains(title, company_core(query.company_name)) and external_company < 2:
                filtered.append(url)
                external_company += 1
            elif not title and unknown < (1 if people_focus else 3):
                filtered.append(url)
                unknown += 1
        ranked = filtered
    profiles = [p.get("profile_url", "") for p in query.people if p.get("profile_url")]
    person_urls = [u for u in ranked if re.search(r"linkedin\.com/(?:in|posts)/", u, re.I)]
    priority = list(dict.fromkeys(profiles + person_urls))
    remaining = [u for u in ranked if u not in priority]
    # Discovered company links must not consume the whole fetch window before
    # a supplied or discovered person profile is tried.
    return list(
        dict.fromkeys(
            (["https://" + query.domain + "/"] if initial else [])
            + (priority + remaining if people_focus else priority[:3] + remaining + priority[3:])
        )
    )


def _search_titles(response):
    data = response.model_dump() if hasattr(response, "model_dump") else response
    titles = {}
    for output in data.get("output", []):
        entries = list((output.get("action") or {}).get("sources") or [])
        for content in output.get("content") or []:
            entries.extend(a for a in content.get("annotations") or [] if a.get("type") == "url_citation")
        for entry in entries:
            if entry.get("url") and entry.get("title"):
                titles[entry["url"]] = entry["title"]
    return titles


def _block_scope(url, code=None):
    from .providers import _fetch_host

    host = _fetch_host(url)
    # A profile's access policy does not describe public posts. Actual rate
    # limits remain host-wide; never rotate regional hosts or retry profiles.
    if host == "linkedin.com" and code != "evidence_http_429":
        kind = urlsplit(url).path.strip("/").split("/", 1)[0]
        if kind in ("in", "posts", "company"):
            return host + "/" + kind
    return host


def retain_unprocessed_sources(bundle, raw_sources):
    """Retain provenance for pending pages without publishing unchecked text."""
    retained = {source.url for source in bundle.sources}
    for raw in raw_sources:
        try:
            source = Source.model_validate(raw)
        except ValueError:
            continue
        if source.url not in retained:
            bundle.sources.append(source.model_copy(update={
                "verified": False,
                "excerpt": "",
                "limitation": "Quelle gespeichert; Faktenextraktion dieser Recherchewelle nicht abgeschlossen.",
            }))
            retained.add(source.url)
    return bundle


def extraction_priority(source, query, people_focus=False):
    """Prefer relevant business/profile text over a long legal document."""
    legal = bool(re.search(
        r"conditions.of.use|terms.of.use|privacy|datenschutz|impressum|imprint|/auth\b|/login\b",
        source.url + " " + source.title, re.I,
    ))
    business = len(set(re.findall(
        r"\b(?:products?|services?|manufactur\w*|entwickel\w*|hersteller\w*|produzier\w*|"
        r"produkte?\w*|leistungen?|lösungen?|bietet|employees?|mitarbeiter\w*)\b",
        source.excerpt.casefold(),
    )))
    person = any(named_person_source(source, p) for p in query.people)
    return (not legal, person if people_focus else business, business, len(source.excerpt))


async def run_research(provider, query, budget, *, checkpoint=None, persist=None):
    from .providers import (
        PUBLIC_INSTRUCTIONS,
        Extraction,
        ProviderError,
        _fetch_host,
        _fetch_key,
        _rank_urls,
        _search_profiles,
        _search_urls,
        validate_extraction,
    )

    if not isinstance(query, ResearchQuery):
        raise TypeError("Recherche akzeptiert ausschließlich öffentliche ResearchQuery.")
    checkpoint = checkpoint or {}
    if checkpoint and checkpoint.get("public_query") != query.model_dump(mode="json"):
        raise ValueError("Recherchecheckpoint gehört zu einer anderen öffentlichen Anfrage.")
    sources = [Source.model_validate(s) for s in checkpoint.get("sources", [])]
    extraction = Extraction.model_validate(checkpoint["extraction"]) if checkpoint.get("extraction") else None
    last = ResearchBundle.model_validate(checkpoint.get("bundle", {}))
    seen, blocked = set(checkpoint.get("seen", [])), set(checkpoint.get("blocked_hosts", []))
    limits = list(checkpoint.get("limits", []))
    waves = int(checkpoint.get("completed_waves", 0))
    active_wave = checkpoint.get("active_wave")
    extracted_sources = set(checkpoint.get("extracted_source_ids", []))
    budget._pdf_fetches = max(getattr(budget, "_pdf_fetches", 0), int(checkpoint.get("pdf_fetches", 0)))
    maximum = getattr(provider.settings, "max_evidence_fetches", 20)
    model_max = getattr(provider.settings, "max_model_requests", 8)
    search_max = getattr(provider.settings, "max_search_calls", 12)

    async def save():
        if not persist:
            return
        snapshot = {
            "version": 2,
            "public_query": query.model_dump(mode="json"),
            "completed_waves": waves,
            "active_wave": active_wave,
            "sources": [s.model_dump(mode="json") for s in sources],
            "extracted_source_ids": sorted(extracted_sources),
            "extraction": extraction.model_dump(mode="json") if extraction else None,
            "bundle": last.model_dump(mode="json"),
            "seen": sorted(seen),
            "blocked_hosts": sorted(blocked),
            "limits": limits,
            "pdf_fetches": getattr(budget, "_pdf_fetches", 0),
            "research_coverage": [c.model_dump(mode="json") for c in last.research_coverage],
            "usage": budget.snapshot()
            if hasattr(budget, "snapshot")
            else budget.usage.model_dump(mode="json"),
        }
        saved = persist(snapshot)
        if inspect.isawaitable(saved):
            saved = await saved
        if saved is False:
            raise LeaseLost("Recherchecheckpoint konnte nicht unter aktueller Lease gespeichert werden.")

    max_waves = 3 if model_max >= 10 else 2
    while waves < max_waves:
        if "provider_insufficient_quota" in budget.usage.errors:
            break
        coverage = research_needs(query, last)
        missing = [c for c in coverage if c.status != "covered"]
        if waves and not missing:
            break
        if (
            _research_seconds(budget) < 5
            or budget.usage.model_requests + (3 if active_wave else 4) > model_max
            or (not active_wave and budget.usage.search_calls + 4 > search_max)
            or (not active_wave and budget.usage.evidence_fetches >= maximum)
        ):
            limits.append(
                "Recherchefenster oder Verbrauchsgrenze erreicht; Zeit und Modellanfragen für Prüfung und Abschluss reserviert."
            )
            break
        count_before = len(sources)
        initial = waves == 0
        try:
            people_focus = not initial and any(c.area == "people" for c in missing) and waves == 1
            data = (
                query.model_dump(mode="json")
                if initial
                else {
                    "public_query": query.model_dump(mode="json"),
                    "research_targets": [reason for area in missing for reason in area.reasons],
                    "missing_areas": [area.area for area in missing],
                    "unavailable_hosts": sorted(host for host in blocked if "/" not in host),
                    "unavailable_resources": sorted(host for host in blocked if "/" in host),
                }
            )
            data = {
                **data,
                "search_queries": public_search_plan(
                    query, people_focus=people_focus, posts_focus="linkedin.com/in" in blocked,
                    missing_areas=[area.area for area in missing] if not initial else None,
                ),
                "research_focus": "people" if people_focus else "company_and_people",
            }
            if not active_wave:
                try:
                    async with provider._search_lock:
                        response = await asyncio.wait_for(
                            provider._request(
                                budget,
                                model=provider.settings.model_research,
                                search=True,
                                reserve_models=3,
                                instructions=PUBLIC_INSTRUCTIONS
                                + "\nBearbeite Unternehmen/Produkte/Märkte, datierte Entwicklungen, berufliche Personen und ICP. Nutze search_queries als konkrete Suchstarts. Jede genannte Person erhält eine eigene Namenssuche; nicht nur im Zusammenhang mit der angefragten Firma suchen. Bei research_focus=people haben Personenprofile, berufliche Stationen und eigene Fachbeiträge Vorrang vor weiteren ICP-Lücken. Andere Arbeitgeber nicht automatisch als Namensverwechslung verwerfen; Firmenzuordnung getrennt prüfen. Öffentlich sichtbare LinkedIn-Profile, Unternehmensseiten und eigene Beiträge als Quellen finden. Gesperrte Hosts und unavailable_resources nicht erneut abrufen. Eine Sperre für linkedin.com/in betrifft Profile, nicht öffentliche /posts/-Beiträge. Keine anderen Firmen allein wegen Namensähnlichkeit, allgemeinen Freelancer-Verzeichnisse oder privaten Inhalte sammeln. Keine erfundenen oder aus Modellwissen ergänzten URLs.",
                                data=data,
                            ),
                            timeout=_research_seconds(budget),
                        )
                except (ProviderError, BudgetExceeded, TimeoutError) as exc:
                    limits.append("Websuche begrenzt: " + getattr(exc, "code", type(exc).__name__))
                    if "provider_insufficient_quota" in budget.usage.errors:
                        raise
                    response = {"output": []}
                queue = evidence_queue(
                    _search_urls(response),
                    query,
                    initial=initial,
                    people_focus=people_focus,
                    titles=_search_titles(response),
                )
                for discovery in _search_profiles(response, query):
                    if not any(s.url == discovery.url for s in sources):
                        sources.append(discovery)
                active_wave = {
                    "queue": queue,
                    "attempts": 0,
                    "initial": initial,
                    "people_focus": people_focus,
                    "count_before": count_before,
                }
                await save()
            queue = active_wave["queue"]
            attempts = active_wave["attempts"]
            initial = active_wave["initial"]
            people_focus = active_wave["people_focus"]
            count_before = active_wave["count_before"]
            wave_limit = 12 if initial else 14
            while (
                queue
                and attempts < wave_limit
                and budget.usage.evidence_fetches < maximum
                # Leave one full model-request window to extract the pages
                # already fetched instead of spending it on one more page.
                and _research_seconds(budget) > 62
            ):
                url = queue.pop(0)
                host, key = _fetch_host(url), _fetch_key(url)
                if key in seen or host in blocked or _block_scope(url) in blocked:
                    continue
                seen.add(key)
                attempts += 1
                try:
                    source = await asyncio.wait_for(
                        provider.fetcher.fetch(url, budget), timeout=min(20, _research_seconds(budget) - 60)
                    )
                    existing = next((i for i, s in enumerate(sources) if s.url == source.url), None)
                    if existing is None:
                        sources.append(source)
                    else:
                        sources[existing] = source
                    own_host = (source.url.split("/", 3)[2].split(":")[0]).lower().removeprefix("www.")
                    domain = query.domain.lower().removeprefix("www.")
                    if own_host == domain or own_host.endswith("." + domain):
                        queue.extend(
                            [
                                u
                                for u in _rank_urls(source.discovered_urls, query.domain)
                                if _fetch_key(u) not in seen
                            ][:4]
                        )
                except asyncio.CancelledError:
                    # The current attempt is counted, but an unfinished page is
                    # still pending after a worker restart.
                    seen.discard(key)
                    queue.insert(0, url)
                    active_wave["attempts"] = attempts
                    await save()
                    raise
                except TimeoutError:
                    limits.append(f"Belegabruf zeitlich begrenzt: {url[:500]}")
                except EvidenceError as exc:
                    limits.append(f"Beleg nicht nutzbar ({exc.code}): {url[:500]}")
                    if exc.code in ("evidence_http_403", "evidence_http_429", "evidence_http_999"):
                        blocked.add(_block_scope(url, exc.code))
                except BudgetExceeded:
                    limits.append("Belegbudget erschöpft; keine weiteren Abrufe.")
                    break
                active_wave["attempts"] = attempts
                await save()
            pending_sources = [s for s in sources if s.verified and s.source_id not in extracted_sources]
            if pending_sources:
                # Retain only established profile context for new stations/posts;
                # already processed company pages must not shrink person windows.
                anchors = [s for group in profile_bindings(query, sources).values() for s in group]
                batch = list({s.source_id: s for s in [*pending_sources, *anchors]}.values())
                processed = pending_sources
                try:
                    extra = await asyncio.wait_for(
                        provider._extract_sources(query, batch, budget),
                        timeout=_research_seconds(budget),
                    )
                except ProviderError as exc:
                    if exc.code != "provider_schema_invalid" or len(batch) < 2:
                        raise
                    # One malformed multi-source answer must not discard every
                    # useful page. Retry one substantial source, under the same
                    # budget and all normal evidence/identity checks.
                    processed = [max(
                        pending_sources, key=lambda source: extraction_priority(source, query, people_focus)
                    )]
                    limits.append(
                        "Faktenextraktion mehrerer Quellen nicht auswertbar; eine Einzelquelle erneut geprüft."
                    )
                    extra = await asyncio.wait_for(
                        provider._extract_sources(query, processed, budget),
                        timeout=_research_seconds(budget),
                    )
                extracted_sources.update(s.source_id for s in processed)
                extraction = Extraction(
                    facts=[*(extraction.facts if extraction else []), *extra.facts],
                    ambiguous_participant_ids=list(
                        dict.fromkeys(
                            [
                                *(extraction.ambiguous_participant_ids if extraction else []),
                                *extra.ambiguous_participant_ids,
                            ]
                        )
                    ),
                    gaps=list(dict.fromkeys([*(extraction.gaps if extraction else []), *extra.gaps])),
                )
                provisional = validate_extraction(extraction, sources, query)
                descriptions = [f for f in provisional.facts if f.subject_ref == "company"
                                and f.field in ("business_model", "services") and f.status == "verified"]
                business_sources = [source for source in pending_sources
                                    if extraction_priority(source, query)[0]
                                    and extraction_priority(source, query)[2] > 0
                                    and contains(source.excerpt, company_core(query.company_name))]
                if (not descriptions and business_sources and _research_seconds(budget) > 40
                        and budget.usage.model_requests + 3 <= model_max):
                    # A usable company page must not be consumed by a response
                    # focused entirely on people. One focused extraction uses
                    # the already fetched page, with no additional search.
                    company_source = max(business_sources, key=lambda source: extraction_priority(source, query))
                    try:
                        company_extra = await asyncio.wait_for(
                            provider._extract_sources(query.model_copy(update={"people": []}), [company_source], budget),
                            timeout=_research_seconds(budget),
                        )
                        extraction.facts.extend(f for f in company_extra.facts if f.subject_ref == "company")
                    except (ProviderError, BudgetExceeded, TimeoutError) as exc:
                        limits.append("Gezielte Firmenquellenauswertung begrenzt: "
                                      + getattr(exc, "code", type(exc).__name__))
        except (ProviderError, BudgetExceeded, TimeoutError) as exc:
            limits.append("Recherchewelle begrenzt: " + getattr(exc, "code", type(exc).__name__))
        active_wave = None
        waves += 1
        if extraction:
            last = _preserve_ids(validate_extraction(extraction, sources, query), last)
        else:
            last = ResearchBundle(
                sources=[
                    s.model_copy(
                        update={
                            "excerpt": "",
                            "verified": False,
                            "limitation": "Abgerufen, aber keine geschäftliche Fundstelle geprüft; Quelltext nicht veröffentlicht.",
                        }
                    )
                    for s in sources
                ],
                gaps=[
                    "Keine konkret geprüften Fakten verfügbar; Suchsnippets werden nicht als Tatsachen übernommen."
                ],
            )
        last.limits = list(dict.fromkeys(limits))
        last.supplemental_search_performed = waves > 1
        last.research_coverage = research_needs(query, last)
        await save()
        # One independent fallback is still useful after a blocked first origin;
        # a second exhausted wave without new material has no further benefit.
        if "provider_insufficient_quota" in budget.usage.errors or (not initial and len(sources) == count_before):
            break
    last.limits = list(dict.fromkeys(limits))
    last.research_coverage = research_needs(query, last)
    return retain_unprocessed_sources(last, [s for s in sources if s.source_id not in extracted_sources])


async def fetch_evidence_urls(provider, query, urls, budget):
    """User-supplied public evidence hints, not an unrestricted search channel."""
    from .providers import ProviderError, validate_extraction

    if not isinstance(query, ResearchQuery):
        raise TypeError("ResearchQuery erforderlich.")
    sources, limits = [], []
    # All redirects/DNS/size/PDF checks still run through EvidenceFetcher.
    for url in list(dict.fromkeys(urls))[:5]:
        try:
            sources.append(await provider.fetcher.fetch(url, budget))
        except EvidenceError as exc:
            limits.append("Öffentlicher Quellenhinweis nicht nutzbar: " + exc.code)
        except BudgetExceeded:
            limits.append("Quellenhinweise wegen Budgetgrenze nicht vollständig geprüft.")
            break
    if not sources:
        return ResearchBundle(gaps=["Kein Quellenhinweis als Beleg verfügbar."], limits=limits)
    try:
        bundle = validate_extraction(await provider._extract_sources(query, sources, budget), sources, query)
    except (ProviderError, BudgetExceeded, TimeoutError):
        return retain_unprocessed_sources(ResearchBundle(
            gaps=["Quellenhinweise abgerufen, Faktenextraktion nicht abgeschlossen."],
            limits=limits + ["Faktenextraktion begrenzt."],
        ), sources)
    bundle.limits.extend(limits)
    bundle.research_coverage = research_needs(query, bundle)
    return bundle
