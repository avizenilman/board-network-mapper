"""LittleSis — free, open database of people and relationships.

Entity search: https://littlesis.org/api/entities/search?q={name}
Relationships: https://littlesis.org/api/entities/{id}/relationships

No authentication required. Rate limit: 2 req/sec.
Covers 400K+ entities and 1.6M+ relationships — board seats, family,
employment, donations, lobbying, ownership, and more.
"""

import asyncio
import ssl
import logging
from typing import Optional

import aiohttp

from sources.base import DataSource
from core.models import BoardSeat, BoardMember, Relationship, OrgRoster
from core.database import Database

logger = logging.getLogger(__name__)

BASE_URL = "https://littlesis.org/api"
REQUEST_DELAY = 0.5  # 2 req/sec

# macOS Python often lacks system certs
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# LittleSis relationship category IDs -> our relationship types
_CATEGORY_MAP = {
    1: "position",       # Position (board member, officer, employee)
    2: "education",      # Education
    3: "membership",     # Membership
    4: "family",         # Family (spouse, parent, child)
    5: "donor",          # Donation/grant
    6: "transaction",    # Transaction
    7: "lobbying",       # Lobbying
    8: "social",         # Social
    9: "professional",   # Professional
    10: "ownership",     # Ownership
    11: "hierarchy",     # Hierarchy
    12: "generic",       # Generic
}

# Map LittleSis categories to our Relationship.relationship_type values
_TYPE_NORMALIZATION = {
    "position": "employee",       # default; refined below by description
    "education": "education",
    "membership": "board_member",
    "family": "spouse",           # default; refined below for parent/child
    "donor": "donor",
    "transaction": "transaction",
    "lobbying": "lobbyist",
    "social": "social",
    "professional": "advisor",
    "ownership": "owner",
    "hierarchy": "employee",
    "generic": "associate",
}

# Position descriptions that indicate board membership
_BOARD_KEYWORDS = frozenset([
    "board", "director", "trustee", "chair", "chairman", "chairwoman",
    "vice chair", "vice chairman", "treasurer", "secretary",
    "board member", "board of directors",
])

# Position descriptions that indicate advisory roles
_ADVISOR_KEYWORDS = frozenset([
    "advisor", "adviser", "advisory", "consultant", "counsel",
])


def _classify_position(description: str) -> str:
    """Classify a LittleSis position description into our relationship type."""
    desc_lower = description.lower().strip()
    if not desc_lower:
        return "employee"

    # Check board keywords first
    for kw in _BOARD_KEYWORDS:
        if kw in desc_lower:
            return "board_member"

    # Check advisory
    for kw in _ADVISOR_KEYWORDS:
        if kw in desc_lower:
            return "advisor"

    return "employee"


def _classify_family(description: str) -> str:
    """Classify a family relationship description."""
    desc_lower = description.lower().strip()
    if any(w in desc_lower for w in ("spouse", "wife", "husband", "partner", "married")):
        return "spouse"
    if any(w in desc_lower for w in ("parent", "father", "mother", "child", "son", "daughter")):
        return "family"
    # Default family relationships to spouse — it's the most common
    # and the most relevant for network mapping (Criterion 18)
    return "spouse"


def _name_from_url(url: str) -> str:
    """Parse an entity name from a LittleSis URL slug.

    E.g. "https://littlesis.org/org/33342-Harvard_Business_School"
         -> "Harvard Business School"
    """
    if not url:
        return ""
    # Grab the path segment after the ID dash: "33342-Harvard_Business_School"
    last_segment = url.rstrip("/").rsplit("/", 1)[-1]
    # Strip the leading numeric ID
    parts = last_segment.split("-", 1)
    if len(parts) == 2:
        return parts[1].replace("_", " ")
    return last_segment.replace("_", " ")


def _entity_name(entity: dict) -> str:
    """Extract the display name from a LittleSis entity dict."""
    attrs = entity.get("attributes", entity)
    return attrs.get("name", attrs.get("blurb", "")).strip()


def _entity_id(entity: dict) -> Optional[int]:
    """Extract the numeric ID from a LittleSis entity dict."""
    attrs = entity.get("attributes", entity)
    eid = attrs.get("id")
    if eid is not None:
        return int(eid)
    return None


class LittleSisSource(DataSource):
    """LittleSis.org data source — people, orgs, and their relationships."""

    name = "littlesis"

    def __init__(self, db: Database = None):
        self.db = db
        self._semaphore = asyncio.Semaphore(2)  # 2 concurrent max

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> dict:
        """Fetch JSON from LittleSis API with rate limiting."""
        async with self._semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            async with session.get(url, ssl=_SSL_CONTEXT) as resp:
                if resp.status == 404:
                    return {}
                resp.raise_for_status()
                return await resp.json()

    # ------------------------------------------------------------------
    # Entity search
    # ------------------------------------------------------------------

    async def _search_entities(self, session: aiohttp.ClientSession,
                               name: str) -> list[dict]:
        """Search LittleSis for entities matching a name.

        Returns the raw list of entity dicts from the API response.
        """
        url = f"{BASE_URL}/entities/search?q={name}"
        try:
            data = await self._fetch_json(session, url)
        except Exception as e:
            logger.warning("LittleSis entity search failed for %r: %s", name, e)
            return []

        entities = data.get("data", [])
        return entities

    # ------------------------------------------------------------------
    # search_person — DataSource interface
    # ------------------------------------------------------------------

    async def search_person(self, name: str) -> list[BoardSeat]:
        """Search LittleSis for entities matching a name.

        Returns BoardSeat objects for any entity that has position
        relationships resembling board seats.
        """
        if self.db:
            cached = self.db.cache_get(self.name, name, "person")
            if cached:
                return [BoardSeat(**s) for s in cached]

        seats: list[BoardSeat] = []

        async with aiohttp.ClientSession() as session:
            entities = await self._search_entities(session, name)

            for entity in entities:
                eid = _entity_id(entity)
                if eid is None:
                    continue

                entity_name = _entity_name(entity)
                attrs = entity.get("attributes", entity)

                # Only process Person entities (not Orgs)
                primary_ext = attrs.get("primary_ext", "")
                if primary_ext and primary_ext != "Person":
                    continue

                # Get this entity's relationships to find board seats
                rels = await self._get_relationships(session, eid)

                for rel in rels:
                    rel_attrs = rel.get("attributes", rel)
                    category_id = rel_attrs.get("category_id")
                    if category_id is None:
                        continue
                    category_id = int(category_id)

                    # We only care about position (1) and membership (3) for BoardSeat
                    if category_id not in (1, 3):
                        continue

                    description1 = rel_attrs.get("description1", "") or ""
                    description2 = rel_attrs.get("description2", "") or ""
                    description = description1 or description2

                    rel_type = _classify_position(description)
                    if category_id == 3:
                        rel_type = "board_member"

                    if rel_type not in ("board_member", "advisor"):
                        continue

                    # Determine the org from the relationship
                    org_name, org_id = self._extract_related_org(
                        rel, eid, entity_name
                    )

                    member_type = "board" if rel_type == "board_member" else "advisory"
                    role = description if description else ("Member" if category_id == 3 else "Director")

                    seat = BoardSeat(
                        person_name=entity_name,
                        org_name=org_name,
                        ein="",  # LittleSis doesn't use EINs
                        role=role,
                        member_type=member_type,
                        tax_period="",
                        source=self.name,
                        is_current=rel_attrs.get("is_current") in (True, 1, "1", None),
                    )
                    seats.append(seat)

        if self.db:
            self.db.cache_set(self.name, name, "person",
                              [s.model_dump() for s in seats])

        return seats

    # ------------------------------------------------------------------
    # search_org — DataSource interface
    # ------------------------------------------------------------------

    async def search_org(self, ein: str) -> OrgRoster:
        """Search LittleSis for an org and return its roster.

        LittleSis doesn't index by EIN, so we search by the EIN string
        as a name query. This is a best-effort lookup — the ProPublica
        source is more reliable for EIN-based searches.
        """
        if self.db:
            cached = self.db.cache_get(self.name, ein, "org")
            if cached:
                return OrgRoster(**cached)

        roster = OrgRoster(
            org_name="", ein=ein, tax_period="", source=self.name
        )

        async with aiohttp.ClientSession() as session:
            # Search for the org entity
            entities = await self._search_entities(session, ein)

            # Find first Org entity
            org_entity = None
            for entity in entities:
                attrs = entity.get("attributes", entity)
                if attrs.get("primary_ext") == "Org":
                    org_entity = entity
                    break

            if not org_entity:
                return roster

            eid = _entity_id(org_entity)
            if eid is None:
                return roster

            roster.org_name = _entity_name(org_entity)

            # Get relationships to find people at this org
            rels = await self._get_relationships(session, eid)

            for rel in rels:
                rel_attrs = rel.get("attributes", rel)
                category_id = rel_attrs.get("category_id")
                if category_id is None:
                    continue
                category_id = int(category_id)

                # Positions and memberships are people at the org
                if category_id not in (1, 3):
                    continue

                person_name, _ = self._extract_related_person(
                    rel, eid, roster.org_name
                )
                if not person_name:
                    continue

                description1 = rel_attrs.get("description1", "") or ""
                description2 = rel_attrs.get("description2", "") or ""
                description = description1 or description2
                role = description if description else "Member"

                rel_type = _classify_position(description)
                if category_id == 3:
                    rel_type = "board_member"

                member_type = "board"
                if rel_type == "employee":
                    member_type = "staff"
                elif rel_type == "advisor":
                    member_type = "advisory"

                member = BoardMember(
                    name=person_name,
                    role=role,
                    member_type=member_type,
                    is_board=(member_type == "board"),
                    is_officer=any(kw in role.lower() for kw in [
                        "chair", "president", "treasurer", "secretary",
                    ]),
                    source=self.name,
                )
                roster.members.append(member)

        if self.db and roster.members:
            self.db.cache_set(self.name, ein, "org", roster.model_dump())

        return roster

    # ------------------------------------------------------------------
    # search_relationships — DataSource interface
    # ------------------------------------------------------------------

    async def search_relationships(self, name: str) -> list[Relationship]:
        """Get ALL relationships for a person from LittleSis.

        Returns board seats, employer, spouse/family, donor, advisor,
        and other relationship types. Spousal and family relationships
        are critical for network mapping (Criterion 18).
        """
        if self.db:
            cached = self.db.cache_get(self.name, name, "relationships")
            if cached:
                return [Relationship(**r) for r in cached]

        relationships: list[Relationship] = []

        async with aiohttp.ClientSession() as session:
            entities = await self._search_entities(session, name)

            for entity in entities:
                eid = _entity_id(entity)
                if eid is None:
                    continue

                entity_name = _entity_name(entity)
                attrs = entity.get("attributes", entity)

                if attrs.get("primary_ext", "") not in ("Person", ""):
                    continue

                rels = await self._get_relationships(session, eid)

                for rel in rels:
                    parsed = self._parse_relationship(rel, eid, entity_name)
                    if parsed:
                        relationships.append(parsed)

                # Only process the first matching Person entity
                # to avoid duplicates from near-name matches
                if relationships:
                    break

        if self.db:
            self.db.cache_set(self.name, name, "relationships",
                              [r.model_dump() for r in relationships])

        return relationships

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_relationships(self, session: aiohttp.ClientSession,
                                 entity_id: int) -> list[dict]:
        """Fetch all relationships for a LittleSis entity."""
        url = f"{BASE_URL}/entities/{entity_id}/relationships"
        try:
            data = await self._fetch_json(session, url)
        except Exception as e:
            logger.warning("LittleSis relationships failed for entity %d: %s",
                           entity_id, e)
            return []

        return data.get("data", [])

    def _extract_related_org(self, rel: dict, source_id: int,
                             source_name: str) -> tuple[str, Optional[int]]:
        """From a relationship dict, extract the entity that isn't the source.

        LittleSis API v2 provides:
          - rel["entity"]  URL for entity1
          - rel["related"] URL for entity2
          - rel["attributes"]["entity1_id"] / entity2_id (numeric)

        There are no entity1_name/entity2_name fields and no "included" list.
        We parse entity names from the URL slugs.

        Returns (related_name, related_id).
        """
        rel_attrs = rel.get("attributes", rel)

        entity1_id = rel_attrs.get("entity1_id")
        entity2_id = rel_attrs.get("entity2_id")

        # Determine which side is the "other" entity
        if entity1_id and int(entity1_id) == source_id:
            # entity1 is our source -> the related entity is entity2
            other_id = entity2_id
            other_url = rel.get("related", "")
        elif entity2_id and int(entity2_id) == source_id:
            # entity2 is our source -> the related entity is entity1
            other_id = entity1_id
            other_url = rel.get("entity", "")
        else:
            # Source not matched on either side — default to entity2
            other_id = entity2_id
            other_url = rel.get("related", "")

        # Parse the name from the URL slug
        other_name = _name_from_url(other_url)

        # Fallback: try to extract from the description field
        # e.g. "Jamie Dimon had a position (Trustee) at Harvard Business School"
        if not other_name:
            desc = rel_attrs.get("description", "")
            if desc and " at " in desc:
                other_name = desc.rsplit(" at ", 1)[-1].strip()
            elif desc:
                other_name = desc

        if not other_name:
            other_name = "Unknown"

        return other_name, int(other_id) if other_id else None

    def _extract_related_person(self, rel: dict, org_id: int,
                                org_name: str) -> tuple[str, Optional[int]]:
        """From a relationship dict, extract the person that isn't the org.

        Returns (person_name, person_id).
        """
        # Same logic as _extract_related_org but from org's perspective
        return self._extract_related_org(rel, org_id, org_name)

    def _parse_relationship(self, rel: dict, source_id: int,
                            source_name: str) -> Optional[Relationship]:
        """Convert a LittleSis relationship dict into our Relationship model."""
        rel_attrs = rel.get("attributes", rel)

        category_id = rel_attrs.get("category_id")
        if category_id is None:
            return None
        category_id = int(category_id)

        category_label = _CATEGORY_MAP.get(category_id, "generic")

        description1 = rel_attrs.get("description1", "") or ""
        description2 = rel_attrs.get("description2", "") or ""
        description = description1 or description2

        # Determine the related entity
        related_name, _ = self._extract_related_org(rel, source_id, source_name)

        # Determine our relationship type
        if category_label == "position":
            rel_type = _classify_position(description)
        elif category_label == "family":
            rel_type = _classify_family(description)
        else:
            rel_type = _TYPE_NORMALIZATION.get(category_label, "associate")

        # Build context string
        context_parts = []
        if description:
            context_parts.append(description)
        if category_label not in ("position", "family", "generic"):
            context_parts.append(f"({category_label})")

        # Year info
        start_date = rel_attrs.get("start_date", "") or ""
        end_date = rel_attrs.get("end_date", "") or ""
        year = ""
        if start_date:
            year = start_date[:4]
        elif end_date:
            year = end_date[:4]

        # Build source URL
        rel_id = rel_attrs.get("id")
        source_url = f"https://littlesis.org/relationships/{rel_id}" if rel_id else ""

        return Relationship(
            person_name=source_name,
            related_to=related_name,
            relationship_type=rel_type,
            context=" ".join(context_parts).strip(),
            source=self.name,
            source_url=source_url,
            confidence="high",
            year=year,
        )
