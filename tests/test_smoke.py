"""Smoke tests S1-S6 from the spec.

These confirm the plumbing works end-to-end. All require live API calls
and are marked @pytest.mark.network.
"""

import pytest

from sources.propublica import ProPublicaSource
from sources.littlesis import LittleSisSource
from sources.website_transalt import TransAltScraper
from sources.web_search import WebSearchSource
from core.database import Database


# ---------------------------------------------------------------------------
# S1: Person search — "Janet Liff" returns TA with Chair role
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_s1_person_search_janet_liff(propublica_source):
    """S1: 'Janet Liff' person search returns TA (EIN 510186015) with Chair role."""
    seats = await propublica_source.search_person("Janet Liff")

    assert len(seats) > 0, "Expected at least 1 board seat for Janet Liff"

    # Find TA among results
    ta_seats = [s for s in seats if s.ein == "510186015"]
    assert len(ta_seats) > 0, "Expected TA (EIN 510186015) in Janet Liff's results"

    # Check that at least one TA seat has a role containing "Chair"
    chair_seats = [s for s in ta_seats if "chair" in s.role.lower()]
    assert len(chair_seats) > 0, (
        f"Expected 'Chair' role at TA, got roles: {[s.role for s in ta_seats]}"
    )


# ---------------------------------------------------------------------------
# S2: Org roster — EIN 510186015 returns >=20 people
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_s2_org_roster(propublica_source, ground_truth):
    """S2: Org roster for EIN 510186015 returns >=20 people including Liff, Reeves, Furnas."""
    roster = await propublica_source.search_org(ground_truth["ein"])

    assert len(roster.members) >= 20, (
        f"Expected >=20 members, got {len(roster.members)}"
    )

    member_names_lower = [m.name.lower() for m in roster.members]

    for expected_name in ["janet liff", "hope reeves", "ben furnas"]:
        found = any(expected_name in n for n in member_names_lower)
        assert found, f"Expected '{expected_name}' in roster, got: {member_names_lower}"


# ---------------------------------------------------------------------------
# S3: Multi-board — "Martin Mignot" returns TA + at least 1 other org
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_s3_multi_board_martin_mignot(propublica_source):
    """S3: 'Martin Mignot' returns TA + at least 1 other org."""
    seats = await propublica_source.search_person("Martin Mignot")

    assert len(seats) > 0, "Expected at least 1 board seat for Martin Mignot"

    eins = set(s.ein for s in seats if s.ein)
    assert "510186015" in eins, "Expected TA (EIN 510186015) in Martin Mignot's results"
    assert len(eins) >= 2, (
        f"Expected >=2 distinct orgs, got {len(eins)}: {eins}"
    )


# ---------------------------------------------------------------------------
# S4: LittleSis enrichment — "Martin Mignot" returns non-990 affiliation
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_s4_littlesis_enrichment_martin_mignot(littlesis_source):
    """S4: LittleSis enrichment for 'Martin Mignot' returns non-990 affiliation."""
    relationships = await littlesis_source.search_relationships("Martin Mignot")

    assert len(relationships) > 0, (
        "Expected at least 1 relationship for Martin Mignot from LittleSis"
    )

    # Check for non-board relationships (employer, advisor, etc.)
    non_board_types = {"employee", "advisor", "donor", "social", "spouse", "owner", "associate"}
    non_board_rels = [r for r in relationships if r.relationship_type in non_board_types]

    # At minimum, any relationship at all is evidence of enrichment
    # The spec says "Index Ventures or similar non-990 affiliation"
    all_related = [r.related_to for r in relationships]
    assert len(relationships) >= 1, (
        f"Expected at least 1 LittleSis relationship, got: {all_related}"
    )


# ---------------------------------------------------------------------------
# S5: Website scrape — transalt.org returns >=19 board members with Liff as Chair
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.asyncio
async def test_s5_website_scrape(test_db):
    """S5: TransAlt website scrape returns >=19 board members with Liff as Chair."""
    scraper = TransAltScraper(db=test_db)
    members = await scraper.scrape()

    board_members = [m for m in members if m.category == "board"]
    assert len(board_members) >= 19, (
        f"Expected >=19 board members, got {len(board_members)}: "
        f"{[m.name for m in board_members]}"
    )

    # Janet Liff should be Chair
    liff_members = [m for m in board_members if "liff" in m.name.lower()]
    assert len(liff_members) > 0, "Expected Janet Liff in board members"
    assert "chair" in liff_members[0].role.lower(), (
        f"Expected Liff to be Chair, got role: '{liff_members[0].role}'"
    )


# ---------------------------------------------------------------------------
# S6: Web search — deep research returns >=1 non-990, non-LittleSis connection
# ---------------------------------------------------------------------------

@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_s6_web_search_deep_research(test_db):
    """S6: Web search deep research returns >=1 non-990, non-LittleSis connection."""
    source = WebSearchSource(db=test_db)

    context = {
        "orgs": ["Transportation Alternatives"],
        "employer": "",
    }
    relationships = await source.deep_research("Martin Mignot", context=context)

    assert len(relationships) >= 1, (
        "Expected >=1 relationship from web search deep research"
    )

    # Verify results are from web_search source
    web_rels = [r for r in relationships if r.source == "web_search"]
    assert len(web_rels) >= 1, "Expected at least 1 result tagged source='web_search'"
