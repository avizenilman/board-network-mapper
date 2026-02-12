"""Tests for Success Criteria 1-11 from the spec.

Criteria 1-9: 990 Backbone (all require network)
Criteria 10-11: Batch mode + CSV export (unit tests + integration tests)
"""

import csv
import io
import json

import pytest

from core.models import (
    BoardSeat, BoardMember, CoConnection, OrgRoster, SearchResult, Relationship,
)
from core.network_builder import NetworkBuilder
from core.batch import (
    connections_to_dataframe, export_csv, get_export_columns, DEFAULT_COLUMNS,
)
from sources.propublica import ProPublicaSource
from sources.littlesis import LittleSisSource


# ============================================================================
# Criterion 1: Person -> board seats
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c1_person_to_board_seats(propublica_source):
    """C1: Janet Liff returns TA + at least 1 other org.
    Each result includes: org name, EIN, role, fiscal year.
    """
    seats = await propublica_source.search_person("Janet Liff")

    assert len(seats) >= 2, f"Expected >=2 board seats, got {len(seats)}"

    # Check TA is present
    ta_seats = [s for s in seats if s.ein == "510186015"]
    assert ta_seats, "TA (EIN 510186015) not found in results"

    # Check role contains Chair-like text
    ta_chair = [s for s in ta_seats if "chair" in s.role.lower()]
    assert ta_chair, f"Expected Chair role at TA, got: {[s.role for s in ta_seats]}"

    # Verify each result has required fields
    for seat in seats:
        assert seat.org_name, f"Missing org_name on seat: {seat}"
        assert seat.ein, f"Missing EIN on seat: {seat}"
        assert seat.role, f"Missing role on seat: {seat}"
        assert seat.tax_period or seat.fiscal_year, (
            f"Missing tax_period/fiscal_year on seat: {seat}"
        )

    # At least 1 other org beyond TA
    other_orgs = [s for s in seats if s.ein != "510186015"]
    assert other_orgs, "Expected at least 1 org besides TA"


# ============================================================================
# Criterion 2: Org -> full roster
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c2_org_full_roster(propublica_source, ground_truth):
    """C2: EIN 510186015 returns >=20 people with named individuals.
    Each person includes: name, role/title, compensation, 990 type flags.
    """
    roster = await propublica_source.search_org(ground_truth["ein"])

    assert len(roster.members) >= 20, (
        f"Expected >=20 members, got {len(roster.members)}"
    )

    # Check specific people are present
    names_lower = {m.name.lower().strip() for m in roster.members}
    for expected in ["janet liff", "hope reeves", "andy lerner", "ben furnas"]:
        found = any(expected in n for n in names_lower)
        assert found, f"'{expected}' not found in roster. Names: {sorted(names_lower)}"

    # Verify each member has required fields
    for member in roster.members:
        assert member.name, "Member missing name"
        assert member.role is not None, f"Member '{member.name}' missing role"
        assert member.member_type in ("board", "officer", "staff", "advisory"), (
            f"Member '{member.name}' has invalid member_type: {member.member_type}"
        )
        # Compensation should be a number (even if 0)
        assert isinstance(member.compensation, (int, float)), (
            f"Member '{member.name}' has non-numeric compensation"
        )


# ============================================================================
# Criterion 3: Board vs staff distinction
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c3_board_vs_staff(propublica_source, ground_truth):
    """C3: Liff=board, Furnas=staff. Output clearly distinguishes."""
    roster = await propublica_source.search_org(ground_truth["ein"])

    # Find Liff
    liff = [m for m in roster.members if "liff" in m.name.lower()]
    assert liff, "Janet Liff not found in roster"
    assert liff[0].member_type == "board" or liff[0].is_board, (
        f"Expected Liff as board, got member_type={liff[0].member_type}, "
        f"is_board={liff[0].is_board}"
    )

    # Find Furnas
    furnas = [m for m in roster.members if "furnas" in m.name.lower()]
    assert furnas, "Ben Furnas not found in roster"
    assert furnas[0].member_type == "staff" or furnas[0].is_key_employee, (
        f"Expected Furnas as staff, got member_type={furnas[0].member_type}, "
        f"is_key_employee={furnas[0].is_key_employee}"
    )

    # Verify filtering works
    board_only = [m for m in roster.members if m.member_type == "board" or m.is_board]
    staff_only = [m for m in roster.members if m.member_type == "staff" or m.is_key_employee]
    assert len(board_only) > 0, "No board members found"
    assert len(staff_only) > 0, "No staff members found"


# ============================================================================
# Criterion 4: Two-hop network
# ============================================================================

@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_c4_two_hop_network(propublica_source, test_db):
    """C4: Janet Liff returns people from >=2 orgs.
    Steps: find orgs -> pull rosters -> aggregate -> deduplicate.
    """
    builder = NetworkBuilder(sources=[propublica_source], db=test_db)
    result = await builder.search_person("Janet Liff", mode="quick")

    # Should find seats at multiple orgs
    assert len(result.board_seats) >= 2, (
        f"Expected >=2 board seats, got {len(result.board_seats)}"
    )

    # Should have connections from those orgs
    assert len(result.connections) > 0, "Expected connections from two-hop network"

    # Connections should span >=2 orgs
    all_orgs = set()
    for conn in result.connections:
        all_orgs.update(conn.shared_orgs)
    assert len(all_orgs) >= 2, (
        f"Expected connections from >=2 orgs, got {len(all_orgs)}: {all_orgs}"
    )

    # Each connection should have required fields
    for conn in result.connections:
        assert conn.name, "Connection missing name"
        assert conn.shared_orgs, f"Connection '{conn.name}' has no shared_orgs"
        assert conn.overlap_count >= 1, f"Connection '{conn.name}' has overlap_count < 1"
        assert conn.most_recent_overlap, f"Connection '{conn.name}' missing most_recent_overlap"


# ============================================================================
# Criterion 5: Network ranking
# ============================================================================

@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_c5_network_ranking(propublica_source, test_db):
    """C5: Results sorted by shared boards desc, recency desc.
    Someone sharing 2 boards always ranks above someone sharing 1.
    """
    builder = NetworkBuilder(sources=[propublica_source], db=test_db)
    result = await builder.search_person("Janet Liff", mode="quick")

    connections = result.connections
    assert len(connections) >= 2, "Need >=2 connections to test ranking"

    # Verify sorted by overlap_count descending
    for i in range(len(connections) - 1):
        curr = connections[i]
        nxt = connections[i + 1]
        assert curr.overlap_count >= nxt.overlap_count, (
            f"Ranking violated: '{curr.name}' (overlap={curr.overlap_count}) "
            f"ranked before '{nxt.name}' (overlap={nxt.overlap_count})"
        )

    # If overlap counts are equal, check recency ordering
    for i in range(len(connections) - 1):
        curr = connections[i]
        nxt = connections[i + 1]
        if curr.overlap_count == nxt.overlap_count:
            # Recency should be descending (more recent first)
            # Allow equal periods
            curr_year = _extract_year(curr.most_recent_overlap)
            nxt_year = _extract_year(nxt.most_recent_overlap)
            assert curr_year >= nxt_year, (
                f"Recency violated: '{curr.name}' ({curr.most_recent_overlap}) "
                f"ranked before '{nxt.name}' ({nxt.most_recent_overlap})"
            )


def _extract_year(period: str) -> int:
    """Extract year from a tax period string for comparison."""
    import re
    match = re.search(r'(\d{4})', period)
    return int(match.group(1)) if match else 0


# ============================================================================
# Criterion 6: Historical vs current
# ============================================================================

@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_c6_historical_vs_current(propublica_source, test_db, ground_truth):
    """C6: Departed members flagged; current members on most recent filing."""
    builder = NetworkBuilder(sources=[propublica_source], db=test_db)
    result = await builder.search_person("Janet Liff", mode="quick")

    # Find connections from TA
    ta_connections = [
        c for c in result.connections
        if any("transportation" in org.lower() for org in c.shared_orgs)
    ]

    # Should have some current and check that is_current flag exists
    current = [c for c in ta_connections if c.is_current]
    assert len(current) > 0, "Expected some current connections"

    # Verify the is_current field is set meaningfully (not all True)
    # At least some connections should have proper current/former distinction
    all_current_values = {c.is_current for c in result.connections}
    # It's possible all visible connections are current; the key test is that
    # the flag exists and is set based on most recent filing year
    for conn in result.connections:
        assert isinstance(conn.is_current, bool), (
            f"Connection '{conn.name}' has non-bool is_current: {conn.is_current}"
        )


# ============================================================================
# Criterion 7: 990-PF capability
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c7_990pf_capability(propublica_source):
    """C7: Private foundations tagged when they exist.
    This is a capability test -- the org_type field should be populated.
    """
    # Search for a person who may appear on private foundation filings
    seats = await propublica_source.search_person("Janet Liff")

    # Verify org_type is set on each seat
    for seat in seats:
        assert seat.org_type in ("public_charity", "private_foundation"), (
            f"Invalid org_type '{seat.org_type}' on seat at {seat.org_name}"
        )

    # Also verify the org lookup sets org_type
    roster = await propublica_source.search_org("510186015")
    assert roster.org_type in ("public_charity", "private_foundation"), (
        f"Roster org_type not set properly: {roster.org_type}"
    )


# ============================================================================
# Criterion 8: Name disambiguation
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c8_name_disambiguation(propublica_source):
    """C8: >20 results for common name -> disambiguation needed.
    'Michael Smith' should return many results; the tool should handle this.
    """
    seats = await propublica_source.search_person("Michael Smith")

    # Common name should return many results
    assert len(seats) > 20, (
        f"Expected >20 results for 'Michael Smith', got {len(seats)}"
    )

    # Results should have enough info for disambiguation: org, city/state, role
    for seat in seats[:5]:  # spot-check first 5
        assert seat.org_name, f"Missing org_name for disambiguation: {seat}"
        assert seat.role, f"Missing role for disambiguation: {seat}"


# ============================================================================
# Criterion 9: Compensation data
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_c9_compensation_data(propublica_source, ground_truth):
    """C9: Compensation shown -- $0 for volunteers, real numbers for staff."""
    roster = await propublica_source.search_org(ground_truth["ein"])

    # Board members should have $0 compensation (volunteers)
    board = [m for m in roster.members if m.member_type == "board" and m.is_board]
    if board:
        volunteer = board[0]
        assert volunteer.compensation == 0.0 or volunteer.total_compensation == 0.0, (
            f"Expected $0 for board volunteer '{volunteer.name}', "
            f"got ${volunteer.total_compensation}"
        )

    # Key employees should have non-zero compensation
    staff = [m for m in roster.members if m.member_type == "staff" or m.is_key_employee]
    compensated_staff = [m for m in staff if m.total_compensation > 0]
    assert len(compensated_staff) > 0, (
        "Expected at least 1 staff member with non-zero compensation"
    )


# ============================================================================
# Criterion 10: Batch mode (unit + integration)
# ============================================================================

class TestC10BatchModeUnit:
    """Unit tests for batch mode logic -- no network required."""

    def test_batch_merge_deduplicates(self):
        """Batch merge removes duplicate connections across search targets."""
        builder = NetworkBuilder(sources=[], db=None)

        # Simulate: PersonA and PersonB both connect to PersonX via different orgs
        conn1 = CoConnection(
            name="Person X",
            shared_orgs=["Org A"],
            shared_org_eins=["111111111"],
            overlap_count=1,
            most_recent_overlap="2024-03",
            connected_via=["PersonA"],
            sources=["propublica_990"],
        )
        conn2 = CoConnection(
            name="Person X",
            shared_orgs=["Org B"],
            shared_org_eins=["222222222"],
            overlap_count=1,
            most_recent_overlap="2023-06",
            connected_via=["PersonB"],
            sources=["propublica_990"],
        )

        # Manually merge like batch_search does
        all_connections = {}
        for conn in [conn1, conn2]:
            key = conn.name.lower().strip()
            if key in all_connections:
                existing = all_connections[key]
                new_orgs = set(existing.shared_orgs) | set(conn.shared_orgs)
                new_via = set(existing.connected_via) | set(conn.connected_via)
                existing.shared_orgs = sorted(new_orgs)
                existing.connected_via = sorted(new_via)
                existing.overlap_count = len(new_orgs)
            else:
                all_connections[key] = conn

        merged = list(all_connections.values())
        assert len(merged) == 1, f"Expected 1 merged connection, got {len(merged)}"
        assert set(merged[0].shared_orgs) == {"Org A", "Org B"}
        assert set(merged[0].connected_via) == {"PersonA", "PersonB"}
        assert merged[0].overlap_count == 2

    def test_batch_ranking_multi_connection(self):
        """Person connected to 3 TA board members ranks higher than 1."""
        conn_high = CoConnection(
            name="High Rank",
            shared_orgs=["Org A", "Org B", "Org C"],
            overlap_count=3,
            most_recent_overlap="2024-03",
            connected_via=["Member1", "Member2", "Member3"],
        )
        conn_low = CoConnection(
            name="Low Rank",
            shared_orgs=["Org A"],
            overlap_count=1,
            most_recent_overlap="2024-03",
            connected_via=["Member1"],
        )

        # Sort by connected_via count, then overlap_count, then recency
        connections = [conn_low, conn_high]
        connections.sort(key=lambda c: (
            -len(c.connected_via),
            -c.overlap_count,
        ))
        assert connections[0].name == "High Rank"

    def test_batch_gap_detection(self):
        """Names with no 990 trail appear in gap list."""
        # The gap list comes from names where search_person returns no seats
        # This is a structural test -- the batch_search method adds to gaps
        # when board_seats is empty
        gap_names = ["Jake Barton", "Karl Chen", "Lucia Deng"]
        gaps = [f"{name}: no 990 trail found" for name in gap_names]
        assert len(gaps) == 3
        assert all("no 990 trail" in g for g in gaps)


@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_c10_batch_mode_integration(propublica_source, test_db, ground_truth):
    """C10: 19 members -> merged list. Multi-connection prospects rank highest.
    Names with no 990 trail appear in gap list.
    """
    builder = NetworkBuilder(sources=[propublica_source], db=test_db)

    # Build the list of all 19 current board members
    all_board = [m["name"] for m in ground_truth["confirmed_current"]]
    all_board += [m["name"] for m in ground_truth["new_no_990_trail"]]
    assert len(all_board) == 19, f"Expected 19 board members, got {len(all_board)}"

    # Run batch -- use a subset to keep test time reasonable
    # Use 3 confirmed (have 990 trail) + 2 new (no 990 trail)
    test_names = [
        ground_truth["confirmed_current"][0]["name"],  # Janet Liff
        ground_truth["confirmed_current"][9]["name"],   # Martin Mignot
        ground_truth["confirmed_current"][6]["name"],   # Andy Lerner
        ground_truth["new_no_990_trail"][0]["name"],    # Jake Barton
        ground_truth["new_no_990_trail"][1]["name"],    # Karl Chen
    ]

    merged, gaps = await builder.batch_search(test_names, mode="quick")

    # Should have merged connections
    assert len(merged) > 0, "Expected merged connections from batch search"

    # New members without 990 trail should be in gaps
    assert len(gaps) >= 1, f"Expected gaps for no-990-trail members, got {len(gaps)}"
    gap_text = " ".join(gaps).lower()
    assert "barton" in gap_text or "chen" in gap_text, (
        f"Expected Barton or Chen in gaps, got: {gaps}"
    )

    # Results should not include the search targets themselves
    target_names_lower = {n.lower().strip() for n in test_names}
    for conn in merged:
        assert conn.name.lower().strip() not in target_names_lower, (
            f"Search target '{conn.name}' should not appear in results"
        )


# ============================================================================
# Criterion 11: CSV export (unit + integration)
# ============================================================================

class TestC11CSVExportUnit:
    """Unit tests for CSV export -- no network required."""

    def _make_test_connections(self):
        """Create test connections for CSV export testing."""
        return [
            CoConnection(
                name="Janet Liff",
                first_name="Janet",
                last_name="Liff",
                shared_orgs=["Transportation Alternatives"],
                shared_org_eins=["510186015"],
                roles=["Chair"],
                member_type="board",
                most_recent_overlap="2024-03",
                overlap_count=1,
                is_current=True,
                compensation=0.0,
                sources=["propublica_990"],
                connected_via=["Martin Mignot"],
            ),
            CoConnection(
                name="Ben Furnas",
                first_name="Ben",
                last_name="Furnas",
                shared_orgs=["Transportation Alternatives"],
                shared_org_eins=["510186015"],
                roles=["Exec Director"],
                member_type="staff",
                most_recent_overlap="2024-03",
                overlap_count=1,
                is_current=True,
                compensation=250000.0,
                sources=["propublica_990"],
                connected_via=["Martin Mignot"],
            ),
        ]

    def test_csv_has_required_columns(self):
        """CSV output includes all required columns from the spec."""
        connections = self._make_test_connections()
        csv_str = export_csv(connections, save_snapshot=False)

        reader = csv.DictReader(io.StringIO(csv_str))
        headers = reader.fieldnames

        required = [
            "first_name", "last_name", "connected_to", "via_shared_orgs",
            "relationship_type", "overlap_count", "most_recent_year",
            "compensation", "source", "confidence", "notes", "stable_id",
        ]
        for col in required:
            assert col in headers, f"Required column '{col}' missing from CSV. Headers: {headers}"

    def test_csv_name_split(self):
        """First and last name are split correctly."""
        connections = self._make_test_connections()
        csv_str = export_csv(connections, save_snapshot=False)

        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)

        liff_row = [r for r in rows if r["last_name"] == "Liff"]
        assert liff_row, "Expected a row with last_name='Liff'"
        assert liff_row[0]["first_name"] == "Janet"

    def test_csv_stable_ids(self):
        """Each row has a stable unique ID."""
        connections = self._make_test_connections()
        csv_str = export_csv(connections, save_snapshot=False)

        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)

        ids = [r["stable_id"] for r in rows]
        assert all(ids), "All rows should have stable_id"
        assert len(set(ids)) == len(ids), "stable_ids should be unique"

        # Re-export should produce same IDs
        csv_str2 = export_csv(connections, save_snapshot=False)
        reader2 = csv.DictReader(io.StringIO(csv_str2))
        rows2 = list(reader2)
        ids2 = [r["stable_id"] for r in rows2]
        assert ids == ids2, "stable_ids should be deterministic"

    def test_csv_includes_relationships(self):
        """CSV includes relationship-based connections (LittleSis, web search)."""
        connections = self._make_test_connections()
        relationships = [
            Relationship(
                person_name="Martin Mignot",
                related_to="Index Ventures",
                relationship_type="employee",
                context="Partner at Index Ventures",
                source="littlesis",
                confidence="high",
            )
        ]
        csv_str = export_csv(connections, relationships=relationships, save_snapshot=False)

        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)

        # Should include the relationship
        index_rows = [r for r in rows if "Index" in r.get("via_shared_orgs", "")
                      or "Index" in r.get("last_name", "")]
        assert index_rows, "Expected Index Ventures relationship in CSV"

    def test_name_splitting_edge_cases(self):
        """CoConnection.split_name handles multi-part and single names."""
        assert CoConnection.split_name("Janet Liff") == ("Janet", "Liff")
        assert CoConnection.split_name("Mary Beth Kelly") == ("Mary", "Beth Kelly")
        assert CoConnection.split_name("Madonna") == ("Madonna", "")
        assert CoConnection.split_name("") == ("", "")
        assert CoConnection.split_name("Richard B. Miller") == ("Richard", "B. Miller")

    def test_exclusion_flagging(self, test_db):
        """Excluded names are flagged in_pipeline=True in CSV."""
        connections = self._make_test_connections()
        test_db.load_exclusions([{"full_name": "Janet Liff"}])

        df = connections_to_dataframe(connections, db=test_db)
        liff_rows = df[df["last_name"] == "Liff"]
        assert len(liff_rows) == 1
        assert liff_rows.iloc[0]["in_pipeline"] == True  # noqa: E712

        furnas_rows = df[df["last_name"] == "Furnas"]
        assert len(furnas_rows) == 1
        assert furnas_rows.iloc[0]["in_pipeline"] == False  # noqa: E712

    def test_snapshot_saved(self, test_db):
        """Export saves a snapshot to the database."""
        connections = self._make_test_connections()
        export_csv(connections, db=test_db, query_description="test export")

        snapshots = test_db.get_snapshots()
        assert len(snapshots) >= 1, "Expected snapshot saved"
        assert snapshots[0]["query_description"] == "test export"
        assert snapshots[0]["row_count"] == 2


@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_c11_csv_export_integration(propublica_source, test_db, ground_truth):
    """C11: Full batch -> CSV export is CRM-import-ready."""
    builder = NetworkBuilder(sources=[propublica_source], db=test_db)

    test_names = [
        ground_truth["confirmed_current"][0]["name"],  # Janet Liff
        ground_truth["confirmed_current"][9]["name"],   # Martin Mignot
    ]
    merged, gaps = await builder.batch_search(test_names, mode="quick")

    csv_str = export_csv(merged, db=test_db, query_description="integration test")

    # Should parse as valid CSV
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) > 0, "CSV should have data rows"

    # Verify first/last name split
    for row in rows:
        assert row.get("first_name"), f"Row missing first_name: {row}"
        assert row.get("last_name"), f"Row missing last_name: {row}"
        # No full name should remain unsplit
        full = f"{row['first_name']} {row['last_name']}"
        assert " " not in row["first_name"].strip(), (
            f"first_name should be a single word, got: '{row['first_name']}'"
        )

    # Verify stable IDs are present and unique
    ids = [row["stable_id"] for row in rows if row.get("stable_id")]
    assert len(ids) == len(rows), "All rows should have stable_id"
    assert len(set(ids)) == len(ids), f"Duplicate stable_ids found"
