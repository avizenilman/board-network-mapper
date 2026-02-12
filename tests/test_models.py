"""Unit tests for Pydantic models and database layer.

All tests in this file run offline -- no network required.
"""

import json
import time

import pytest

from core.models import (
    BoardMember, BoardSeat, CoConnection, Relationship,
    SearchResult, OrgRoster, WebsiteMember,
)
from core.database import Database
from core.network_builder import NetworkBuilder


# ============================================================================
# Model creation and validation
# ============================================================================

class TestBoardMember:
    def test_create_minimal(self):
        m = BoardMember(name="Janet Liff", role="Chair", member_type="board")
        assert m.name == "Janet Liff"
        assert m.compensation == 0.0

    def test_total_compensation(self):
        m = BoardMember(
            name="Ben Furnas", role="Exec Director", member_type="staff",
            compensation=180000.0, compensation_related=20000.0, compensation_other=5000.0,
        )
        assert m.total_compensation == 205000.0

    def test_defaults(self):
        m = BoardMember(name="X", role="Y", member_type="board")
        assert m.is_board is False
        assert m.is_officer is False
        assert m.is_key_employee is False
        assert m.is_former is False
        assert m.source == ""


class TestBoardSeat:
    def test_stable_id_deterministic(self):
        seat = BoardSeat(
            person_name="Janet Liff", org_name="TA", ein="510186015",
            role="Chair", member_type="board", tax_period="2024-03",
        )
        id1 = seat.stable_id
        id2 = seat.stable_id
        assert id1 == id2
        assert len(id1) == 16

    def test_stable_id_case_insensitive(self):
        s1 = BoardSeat(
            person_name="Janet Liff", org_name="TA", ein="510186015",
            role="Chair", member_type="board", tax_period="2024-03",
        )
        s2 = BoardSeat(
            person_name="JANET LIFF", org_name="TA", ein="510186015",
            role="Chair", member_type="board", tax_period="2024-03",
        )
        assert s1.stable_id == s2.stable_id

    def test_total_compensation(self):
        seat = BoardSeat(
            person_name="X", org_name="Y", ein="1", role="R",
            member_type="staff", tax_period="2024",
            compensation=100.0, compensation_related=50.0, compensation_other=25.0,
        )
        assert seat.total_compensation == 175.0

    def test_org_type_default(self):
        seat = BoardSeat(
            person_name="X", org_name="Y", ein="1", role="R",
            member_type="board", tax_period="2024",
        )
        assert seat.org_type == "public_charity"


class TestCoConnection:
    def test_split_name_standard(self):
        assert CoConnection.split_name("Janet Liff") == ("Janet", "Liff")

    def test_split_name_multi_part(self):
        assert CoConnection.split_name("Mary Beth Kelly") == ("Mary", "Beth Kelly")

    def test_split_name_single(self):
        assert CoConnection.split_name("Prince") == ("Prince", "")

    def test_split_name_empty(self):
        assert CoConnection.split_name("") == ("", "")

    def test_split_name_with_suffix(self):
        first, last = CoConnection.split_name("Richard B. Miller")
        assert first == "Richard"
        assert last == "B. Miller"

    def test_stable_id_uses_eins(self):
        c1 = CoConnection(
            name="X", shared_org_eins=["111", "222"],
        )
        c2 = CoConnection(
            name="X", shared_org_eins=["222", "111"],
        )
        # Should be same because EINs are sorted
        assert c1.stable_id == c2.stable_id

    def test_stable_id_different_eins(self):
        c1 = CoConnection(name="X", shared_org_eins=["111"])
        c2 = CoConnection(name="X", shared_org_eins=["222"])
        assert c1.stable_id != c2.stable_id


class TestRelationship:
    def test_create(self):
        r = Relationship(
            person_name="Daniel Kaizer",
            related_to="Adam Moss",
            relationship_type="spouse",
            source="littlesis",
        )
        assert r.confidence == "high"
        assert r.source_url == ""


class TestSearchResult:
    def test_defaults(self):
        sr = SearchResult(query_name="Janet Liff")
        assert sr.mode == "quick"
        assert sr.board_seats == []
        assert sr.connections == []
        assert sr.gaps == []
        assert sr.sources_failed == []


class TestOrgRoster:
    def test_defaults(self):
        r = OrgRoster(org_name="TA", ein="510186015", tax_period="2024-03")
        assert r.members == []
        assert r.org_type == "public_charity"


class TestWebsiteMember:
    def test_defaults(self):
        m = WebsiteMember(name="Janet Liff")
        assert m.category == "board"
        assert m.role == ""


# ============================================================================
# Database layer
# ============================================================================

class TestDatabase:
    def test_cache_roundtrip(self, test_db):
        """Cache set then get returns same data."""
        data = {"seats": [{"name": "Janet Liff", "role": "Chair"}]}
        test_db.cache_set("propublica", "janet liff", "person", data)
        result = test_db.cache_get("propublica", "janet liff", "person")
        assert result == data

    def test_cache_case_insensitive(self, test_db):
        """Cache key is case-insensitive."""
        test_db.cache_set("propublica", "Janet Liff", "person", {"test": True})
        result = test_db.cache_get("propublica", "janet liff", "person")
        assert result is not None

    def test_cache_miss(self, test_db):
        """Cache miss returns None."""
        result = test_db.cache_get("propublica", "nobody", "person")
        assert result is None

    def test_cache_ttl_expiry(self, test_db):
        """Expired cache entries are deleted and return None."""
        # Set TTL to 1 second
        test_db.ttl = 1
        test_db.cache_set("src", "q", "t", {"data": True})

        # Should be found immediately
        assert test_db.cache_get("src", "q", "t") is not None

        # Wait for expiry
        time.sleep(1.1)
        assert test_db.cache_get("src", "q", "t") is None

    def test_cache_age(self, test_db):
        """cache_age returns seconds since cached."""
        test_db.cache_set("src", "q", "t", {"data": True})
        age = test_db.cache_age("src", "q", "t")
        assert age is not None
        assert age < 2.0  # should be nearly instant

    def test_cache_age_missing(self, test_db):
        assert test_db.cache_age("src", "missing", "t") is None

    def test_search_log(self, test_db):
        """Search log records and retrieves searches."""
        test_db.log_search("Janet Liff", 25, mode="quick",
                           sources_used=["propublica_990"])
        history = test_db.get_search_history()
        assert len(history) == 1
        assert history[0]["query_name"] == "Janet Liff"
        assert history[0]["result_count"] == 25
        assert history[0]["mode"] == "quick"

    def test_search_log_ordering(self, test_db):
        """Search history is ordered by most recent first."""
        test_db.log_search("first", 1)
        time.sleep(0.01)
        test_db.log_search("second", 2)
        history = test_db.get_search_history()
        assert history[0]["query_name"] == "second"
        assert history[1]["query_name"] == "first"

    def test_snapshot_roundtrip(self, test_db):
        """Snapshot save and retrieve."""
        csv_data = "first_name,last_name\nJanet,Liff\n"
        test_db.save_snapshot(csv_data, query_description="test", row_count=1)
        snapshots = test_db.get_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["row_count"] == 1

        data = test_db.get_snapshot_data(snapshots[0]["id"])
        assert data == csv_data

    def test_validation_roundtrip(self, test_db):
        """Validation save and retrieve."""
        test_db.save_validation("510186015", "Janet Liff", "confirmed",
                                on_990=True, on_website=True)
        vals = test_db.get_validations("510186015")
        assert len(vals) == 1
        assert vals[0]["person_name"] == "Janet Liff"
        assert vals[0]["status"] == "confirmed"
        assert vals[0]["on_990"] == 1
        assert vals[0]["on_website"] == 1

    def test_exclusion_list(self, test_db):
        """Load and query exclusion list."""
        test_db.load_exclusions([
            {"full_name": "Janet Liff"},
            {"first_name": "Ben", "last_name": "Furnas"},
        ])
        assert test_db.is_excluded("Janet Liff")
        assert test_db.is_excluded("Ben Furnas")
        assert not test_db.is_excluded("Martin Mignot")

    def test_exclusion_case_insensitive(self, test_db):
        """Exclusion matching is case-insensitive."""
        test_db.load_exclusions([{"full_name": "Janet Liff"}])
        assert test_db.is_excluded("janet liff")
        assert test_db.is_excluded("JANET LIFF")

    def test_exclusion_clear(self, test_db):
        """Clear exclusions removes all entries."""
        test_db.load_exclusions([{"full_name": "Janet Liff"}])
        assert test_db.is_excluded("Janet Liff")
        test_db.clear_exclusions()
        assert not test_db.is_excluded("Janet Liff")

    def test_get_excluded_names(self, test_db):
        """Get all excluded names."""
        test_db.load_exclusions([
            {"full_name": "Janet Liff"},
            {"full_name": "Ben Furnas"},
        ])
        names = test_db.get_excluded_names()
        assert "Janet Liff" in names
        assert "Ben Furnas" in names


# ============================================================================
# NetworkBuilder unit tests (ranking logic, name matching)
# ============================================================================

class TestNetworkBuilderLogic:
    """Test NetworkBuilder's internal logic without network calls."""

    def test_names_match_exact(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._names_match("janet liff", "janet liff")

    def test_names_match_last_plus_initial(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._names_match("j liff", "janet liff")

    def test_names_dont_match(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert not builder._names_match("janet liff", "janet smith")

    def test_primary_member_type_board(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._primary_member_type({"board", "officer"}) == "board"

    def test_primary_member_type_staff(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._primary_member_type({"staff", "board"}) == "staff"

    def test_primary_member_type_advisory(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._primary_member_type({"advisory"}) == "advisory"

    def test_year_from_period(self):
        builder = NetworkBuilder(sources=[], db=None)
        assert builder._year_from_period("2024-03") == 2024
        assert builder._year_from_period("2023") == 2023
        assert builder._year_from_period("") == 0

    def test_build_connections_ranking(self):
        """_build_connections sorts by overlap_count desc, then recency desc."""
        builder = NetworkBuilder(sources=[], db=None)

        # Create rosters for 2 orgs
        roster1 = OrgRoster(
            org_name="Org A", ein="111", tax_period="2024-03",
            fiscal_year=2024,
            members=[
                BoardMember(name="Janet Liff", role="Chair", member_type="board"),
                BoardMember(name="Person X", role="Director", member_type="board"),
                BoardMember(name="Person Y", role="Director", member_type="board"),
            ],
            source="propublica_990",
        )
        roster2 = OrgRoster(
            org_name="Org B", ein="222", tax_period="2023-06",
            fiscal_year=2023,
            members=[
                BoardMember(name="Janet Liff", role="Member", member_type="board"),
                BoardMember(name="Person X", role="Director", member_type="board"),
                BoardMember(name="Person Z", role="Director", member_type="board"),
            ],
            source="propublica_990",
        )

        seats = [
            BoardSeat(person_name="Janet Liff", org_name="Org A", ein="111",
                       role="Chair", member_type="board", tax_period="2024-03"),
            BoardSeat(person_name="Janet Liff", org_name="Org B", ein="222",
                       role="Member", member_type="board", tax_period="2023-06"),
        ]

        connections = builder._build_connections(
            "Janet Liff",
            seats,
            {"111": [roster1], "222": [roster2]},
        )

        # Person X appears in both orgs -> overlap_count=2, should rank first
        names = [c.name for c in connections]
        assert "Person X" in names
        x_conn = [c for c in connections if c.name == "Person X"][0]
        assert x_conn.overlap_count == 2

        # Person X should rank above Person Y and Z (who each have 1)
        x_idx = names.index("Person X")
        assert x_idx == 0, f"Person X should be first, but is at index {x_idx}: {names}"

    def test_build_research_context(self):
        """_build_research_context creates proper context dict."""
        builder = NetworkBuilder(sources=[], db=None)

        seats = [
            BoardSeat(person_name="M", org_name="TA", ein="1",
                       role="Dir", member_type="board", tax_period="2024"),
            BoardSeat(person_name="M", org_name="Tech:NYC", ein="2",
                       role="Dir", member_type="board", tax_period="2024"),
        ]
        relationships = [
            Relationship(
                person_name="Martin Mignot",
                related_to="Index Ventures",
                relationship_type="employee",
            )
        ]
        ctx = builder._build_research_context("Martin Mignot", seats, relationships)

        assert ctx["name"] == "Martin Mignot"
        assert ctx["employer"] == "Index Ventures"
        assert "TA" in ctx["orgs"]
        assert "Tech:NYC" in ctx["orgs"]
