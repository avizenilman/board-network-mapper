"""Network builder: two-hop board co-service network.

person → orgs (via 990) → all co-members → deduplicate → rank
"""

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.models import (
    BoardSeat, BoardMember, CoConnection, SearchResult,
    Relationship, OrgRoster,
)
from core.database import Database
from sources.base import DataSource


class NetworkBuilder:
    def __init__(self, sources: list[DataSource], db: Database = None):
        self.sources = sources
        self.db = db

    async def search_person(self, name: str, mode: str = "quick") -> SearchResult:
        """Full person lookup: find board seats, co-members, relationships.

        mode='quick': 990 + LittleSis (cached)
        mode='deep': + web search enrichment
        """
        result = SearchResult(
            query_name=name,
            timestamp=datetime.now(),
            mode=mode,
        )

        # Step 1: Find all board seats for this person across sources
        all_seats: list[BoardSeat] = []
        for source in self.sources:
            try:
                seats = await source.search_person(name)
                all_seats.extend(seats)
                result.sources_used.append(source.name)
            except Exception as e:
                result.sources_failed.append(f"{source.name}: {str(e)}")

        # Deduplicate seats by (ein, tax_period)
        seen = set()
        unique_seats = []
        for seat in all_seats:
            key = (seat.ein, seat.tax_period, seat.person_name.lower())
            if key not in seen:
                seen.add(key)
                unique_seats.append(seat)
        result.board_seats = unique_seats

        # Step 2: For each org, get the full roster
        org_eins = list(set(s.ein for s in unique_seats if s.ein))
        org_rosters: dict[str, list[OrgRoster]] = {}
        for ein in org_eins:
            for source in self.sources:
                try:
                    if hasattr(source, 'search_org_all_years'):
                        rosters = await source.search_org_all_years(ein)
                    else:
                        roster = await source.search_org(ein)
                        rosters = [roster] if roster.members else []
                    if rosters:
                        org_rosters[ein] = rosters
                        break  # got data from this source
                except Exception:
                    continue

        # Step 3: Build co-connections
        result.connections = self._build_connections(name, unique_seats, org_rosters)

        # Step 4: Get additional relationships (LittleSis, etc.)
        for source in self.sources:
            try:
                rels = await source.search_relationships(name)
                result.relationships.extend(rels)
            except Exception:
                pass

        # Step 5: Deep research mode
        if mode == "deep":
            context = self._build_research_context(name, unique_seats, result.relationships)
            for source in self.sources:
                try:
                    deep_rels = await source.deep_research(name, context)
                    result.relationships.extend(deep_rels)
                except Exception:
                    pass

        # Log search
        if self.db:
            self.db.log_search(
                name,
                len(result.connections),
                mode=mode,
                sources_used=result.sources_used,
                sources_failed=result.sources_failed,
            )

        return result

    def _build_connections(
        self,
        query_name: str,
        seats: list[BoardSeat],
        org_rosters: dict[str, list[OrgRoster]],
    ) -> list[CoConnection]:
        """Build ranked co-connection list from board seats and org rosters."""
        query_lower = query_name.lower().strip()

        # Aggregate: person_name_lower → {data}
        connections: dict[str, dict] = defaultdict(lambda: {
            "name": "",
            "shared_orgs": set(),
            "shared_org_eins": set(),
            "roles": set(),
            "member_types": set(),
            "most_recent_year": 0,
            "most_recent_period": "",
            "is_current": False,
            "compensation": 0.0,
            "sources": set(),
        })

        for ein, rosters in org_rosters.items():
            if not rosters:
                continue

            # Get the org name from first roster
            org_name = rosters[0].org_name

            # Most recent roster determines "current"
            most_recent_fy = max(r.fiscal_year for r in rosters)

            for roster in rosters:
                for member in roster.members:
                    member_lower = member.name.lower().strip()
                    # Skip the query person themselves
                    if self._names_match(member_lower, query_lower):
                        continue

                    key = member_lower
                    conn = connections[key]
                    conn["name"] = member.name
                    conn["shared_orgs"].add(org_name)
                    conn["shared_org_eins"].add(ein)
                    conn["roles"].add(member.role)
                    conn["member_types"].add(member.member_type)
                    conn["sources"].add(roster.source)
                    conn["compensation"] = max(conn["compensation"], member.total_compensation)

                    if roster.fiscal_year > conn["most_recent_year"]:
                        conn["most_recent_year"] = roster.fiscal_year
                        conn["most_recent_period"] = roster.tax_period

                    # Current if on the most recent roster for this org
                    if roster.fiscal_year == most_recent_fy and not member.is_former:
                        conn["is_current"] = True

        # Convert to CoConnection list
        result = []
        for key, data in connections.items():
            first, last = CoConnection.split_name(data["name"])
            co = CoConnection(
                name=data["name"],
                first_name=first,
                last_name=last,
                shared_orgs=sorted(data["shared_orgs"]),
                shared_org_eins=sorted(data["shared_org_eins"]),
                roles=sorted(data["roles"]),
                member_type=self._primary_member_type(data["member_types"]),
                most_recent_overlap=data["most_recent_period"],
                overlap_count=len(data["shared_orgs"]),
                is_current=data["is_current"],
                compensation=data["compensation"],
                sources=sorted(data["sources"]),
                confidence="high",
                connected_via=[query_name],
            )
            result.append(co)

        # Sort: shared boards desc, then recency desc
        result.sort(key=lambda c: (-c.overlap_count, -self._year_from_period(c.most_recent_overlap)))

        return result

    def _names_match(self, a: str, b: str) -> bool:
        """Check if two name strings likely refer to the same person."""
        a, b = a.strip(), b.strip()
        if a == b:
            return True
        # Check last name + first initial match
        a_parts = a.split()
        b_parts = b.split()
        if len(a_parts) >= 2 and len(b_parts) >= 2:
            if a_parts[-1] == b_parts[-1] and a_parts[0][0] == b_parts[0][0]:
                return True
        return False

    def _primary_member_type(self, types: set[str]) -> str:
        """Pick the primary member type from a set."""
        if "staff" in types:
            return "staff"
        if "board" in types:
            return "board"
        if "advisory" in types:
            return "advisory"
        return "board"

    def _year_from_period(self, period: str) -> int:
        """Extract year from a tax period string."""
        import re
        match = re.search(r'(\d{4})', period)
        return int(match.group(1)) if match else 0

    def _build_research_context(
        self,
        name: str,
        seats: list[BoardSeat],
        relationships: list[Relationship],
    ) -> dict:
        """Build context dict for deep research queries."""
        orgs = list(set(s.org_name for s in seats))
        employers = [
            r.related_to for r in relationships
            if r.relationship_type == "employee"
        ]
        return {
            "name": name,
            "orgs": orgs,
            "employer": employers[0] if employers else "",
            "employers": employers,
        }

    async def search_org(self, ein: str) -> OrgRoster:
        """Get org roster from available sources."""
        for source in self.sources:
            try:
                roster = await source.search_org(ein)
                if roster.members:
                    return roster
            except Exception:
                continue
        return OrgRoster(org_name="", ein=ein, tax_period="", source="")

    async def batch_search(
        self,
        names: list[str],
        mode: str = "quick",
    ) -> tuple[list[CoConnection], list[str]]:
        """Search multiple people, merge results, deduplicate.

        Returns (merged_connections, gap_list).
        Gap list = names with no 990 trail.
        """
        all_connections: dict[str, CoConnection] = {}
        gaps = []

        for name in names:
            result = await self.search_person(name, mode=mode)

            if not result.board_seats:
                gaps.append(f"{name}: no 990 trail found")
                continue

            for conn in result.connections:
                key = conn.name.lower().strip()
                if key in all_connections:
                    existing = all_connections[key]
                    # Merge: add shared orgs, update connected_via
                    new_orgs = set(existing.shared_orgs) | set(conn.shared_orgs)
                    new_eins = set(existing.shared_org_eins) | set(conn.shared_org_eins)
                    new_via = set(existing.connected_via) | set(conn.connected_via)
                    new_roles = set(existing.roles) | set(conn.roles)
                    new_sources = set(existing.sources) | set(conn.sources)

                    existing.shared_orgs = sorted(new_orgs)
                    existing.shared_org_eins = sorted(new_eins)
                    existing.connected_via = sorted(new_via)
                    existing.roles = sorted(new_roles)
                    existing.sources = sorted(new_sources)
                    existing.overlap_count = len(new_orgs)
                    existing.is_current = existing.is_current or conn.is_current
                    existing.compensation = max(existing.compensation, conn.compensation)

                    # Keep the most recent overlap
                    if self._year_from_period(conn.most_recent_overlap) > \
                       self._year_from_period(existing.most_recent_overlap):
                        existing.most_recent_overlap = conn.most_recent_overlap
                else:
                    all_connections[key] = conn

        # Remove the search targets themselves from results
        target_names = {n.lower().strip() for n in names}
        merged = [
            c for c in all_connections.values()
            if c.name.lower().strip() not in target_names
        ]

        # Sort: connected to most TA members first, then shared orgs, then recency
        merged.sort(key=lambda c: (
            -len(c.connected_via),
            -c.overlap_count,
            -self._year_from_period(c.most_recent_overlap),
        ))

        return merged, gaps
