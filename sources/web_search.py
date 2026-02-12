"""Web search enrichment — structured queries for deep research mode.

Uses duckduckgo-search for web queries. Parses results into structured
Relationship objects with confidence tiering.
"""

import asyncio
import re
import ssl
import time
from typing import Optional

from core.models import Relationship
from core.database import Database
from sources.base import DataSource

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

RATE_LIMIT_DELAY = 1.5  # seconds between requests
MAX_RESULTS_PER_QUERY = 10


def _build_queries(name: str, context: dict = None) -> list[tuple[str, str]]:
    """Build structured search queries for a person.

    Returns list of (query_string, query_type) tuples.
    """
    context = context or {}
    employer = context.get("employer", "")
    orgs = context.get("orgs", [])

    queries = [
        (f'"{name}" board director nonprofit', "board"),
        (f'"{name}" advisory council', "advisory"),
    ]

    if employer:
        queries.append((f'"{name}" "{employer}"', "employer"))
        queries.append((f'"{employer}" foundation philanthropy nonprofit', "employer_network"))

    for org in orgs[:2]:
        queries.append((f'"{name}" "{org}"', "org_context"))

    queries.append((f'"{name}" gala OR benefit OR fundraiser NYC', "events"))

    return queries


def _parse_search_result(
    result: dict,
    person_name: str,
    query_type: str,
) -> list[Relationship]:
    """Parse a single search result into Relationship objects."""
    relationships = []

    title = result.get("title", "")
    body = result.get("body", result.get("snippet", ""))
    url = result.get("href", result.get("link", ""))

    text = f"{title} {body}".lower()

    # Skip irrelevant results
    if person_name.lower().split()[-1] not in text:
        return []

    # Extract relationship type based on query type and content
    if query_type == "board":
        # Look for board/director mentions
        board_patterns = [
            r"board (?:of directors|member|chair)",
            r"(?:trustee|director) (?:of|at)",
            r"appointed to.*board",
            r"serves on.*board",
        ]
        for pattern in board_patterns:
            if re.search(pattern, text):
                # Try to extract org name
                org_match = re.search(
                    r"(?:board of|director (?:of|at)|trustee of|serves on.*?board of)\s+(?:the\s+)?([A-Z][^,.\n]{3,50})",
                    f"{title} {body}",
                )
                org_name = org_match.group(1).strip() if org_match else title[:60]
                relationships.append(Relationship(
                    person_name=person_name,
                    related_to=org_name,
                    relationship_type="board_member",
                    context=f"Found via web search: {title[:100]}",
                    source="web_search",
                    source_url=url,
                    confidence="medium",
                ))
                break

    elif query_type == "advisory":
        if "advisory" in text:
            org_match = re.search(
                r"advisory (?:council|board|committee) (?:of|at|for)\s+(?:the\s+)?([A-Z][^,.\n]{3,50})",
                f"{title} {body}",
            )
            org_name = org_match.group(1).strip() if org_match else title[:60]
            relationships.append(Relationship(
                person_name=person_name,
                related_to=org_name,
                relationship_type="advisor",
                context=f"Advisory role: {title[:100]}",
                source="web_search",
                source_url=url,
                confidence="medium",
            ))

    elif query_type == "employer":
        if any(kw in text for kw in ["partner", "managing", "principal", "founder", "ceo", "president", "director"]):
            relationships.append(Relationship(
                person_name=person_name,
                related_to=title[:60],
                relationship_type="employee",
                context=f"Employer connection: {title[:100]}",
                source="web_search",
                source_url=url,
                confidence="medium",
            ))

    elif query_type == "employer_network":
        # Look for other people at the same employer who are involved in nonprofits
        name_pattern = r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"
        names_found = re.findall(name_pattern, f"{title} {body}")
        for found_name in names_found[:3]:
            if found_name.lower() != person_name.lower() and len(found_name.split()) >= 2:
                relationships.append(Relationship(
                    person_name=person_name,
                    related_to=found_name,
                    relationship_type="colleague",
                    context=f"Employer network: {title[:100]}",
                    source="web_search",
                    source_url=url,
                    confidence="low",
                ))

    elif query_type == "events":
        if any(kw in text for kw in ["gala", "benefit", "fundraiser", "panel", "event", "dinner", "awards"]):
            # Try to extract event name and co-attendees
            relationships.append(Relationship(
                person_name=person_name,
                related_to=title[:80],
                relationship_type="event",
                context=f"Event co-appearance: {body[:150]}",
                source="web_search",
                source_url=url,
                confidence="low",
            ))

    # If nothing specific matched but the result is relevant
    if not relationships and person_name.lower().split()[-1] in text:
        # Generic connection
        relationships.append(Relationship(
            person_name=person_name,
            related_to=title[:80],
            relationship_type="mention",
            context=body[:200] if body else title,
            source="web_search",
            source_url=url,
            confidence="low",
        ))

    return relationships


class WebSearchSource(DataSource):
    """Web search enrichment for deep research mode."""

    name = "web_search"

    def __init__(self, db: Database = None):
        self.db = db
        self._last_request_time = 0

    async def search_person(self, name: str):
        """Not used for web search — use deep_research instead."""
        return []

    async def search_org(self, ein: str):
        """Not applicable for web search."""
        from core.models import OrgRoster
        return OrgRoster(org_name="", ein=ein, tax_period="", source=self.name)

    async def search_relationships(self, name: str) -> list[Relationship]:
        """Basic web search for relationships."""
        return await self.deep_research(name)

    async def deep_research(self, name: str, context: dict = None) -> list[Relationship]:
        """Run structured web queries for a person.

        Returns parsed Relationship objects with source URLs and confidence.
        """
        # Check cache
        cache_key = f"{name}|{str(context or {})}"
        if self.db:
            cached = self.db.cache_get(self.name, cache_key, "deep")
            if cached:
                return [Relationship(**r) for r in cached]

        queries = _build_queries(name, context)
        all_relationships: list[Relationship] = []

        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return all_relationships

        for query_str, query_type in queries:
            # Rate limiting
            await self._rate_limit()

            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda q=query_str: list(DDGS().text(q, max_results=MAX_RESULTS_PER_QUERY)),
                )

                for result in results:
                    rels = _parse_search_result(result, name, query_type)
                    all_relationships.extend(rels)

            except Exception:
                continue

        # Deduplicate by (person_name, related_to, relationship_type)
        seen = set()
        unique = []
        for rel in all_relationships:
            key = (rel.person_name.lower(), rel.related_to.lower(), rel.relationship_type)
            if key not in seen:
                seen.add(key)
                unique.append(rel)

        # Cache results
        if self.db and unique:
            self.db.cache_set(self.name, cache_key, "deep",
                              [r.model_dump() for r in unique])

        return unique

    async def _rate_limit(self):
        """Enforce rate limiting between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()
