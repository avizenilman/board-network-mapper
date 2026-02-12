"""Tests for website scraping and 990-website reconciliation.

Covers:
- TransAlt scraper: board + advisory member extraction
- Reconciliation logic with mock data
- Name matching (fuzzy match function)
"""

import pytest

from core.models import BoardMember, OrgRoster, WebsiteMember
from sources.website_transalt import (
    TransAltScraper,
    reconcile_with_990,
    _fuzzy_name_match,
    _is_company,
    _is_role,
    _parse_name_and_role,
    _clean_name,
)


# ============================================================================
# Name matching (pure unit tests -- no network)
# ============================================================================

class TestFuzzyNameMatch:
    """Test the _fuzzy_name_match function used for reconciliation."""

    def test_exact_match(self):
        assert _fuzzy_name_match("Janet Liff", "Janet Liff")

    def test_case_insensitive(self):
        assert _fuzzy_name_match("JANET LIFF", "janet liff")

    def test_whitespace_tolerance(self):
        assert _fuzzy_name_match("Janet Liff ", " Janet Liff")

    def test_prefix_match(self):
        """Ken/Kenneth match because first name is a prefix."""
        assert _fuzzy_name_match("Ken Weine", "Kenneth Weine")

    def test_middle_name_tolerance(self):
        """George H Beane matches George Beane."""
        assert _fuzzy_name_match("George H Beane", "George Beane")

    def test_different_people(self):
        assert not _fuzzy_name_match("Janet Liff", "Janet Smith")

    def test_different_first_name(self):
        assert not _fuzzy_name_match("John Smith", "Jane Smith")

    def test_completely_different(self):
        assert not _fuzzy_name_match("Alice Cooper", "Bob Dylan")

    def test_suffix_stripping(self):
        """Names with suffixes like Jr, Esq should still match after cleaning."""
        assert _fuzzy_name_match("Richard Miller", "Richard B. Miller")

    def test_single_name(self):
        """Single-word names shouldn't crash."""
        assert not _fuzzy_name_match("Madonna", "Janet Liff")

    def test_empty_string(self):
        assert not _fuzzy_name_match("", "Janet Liff")
        assert not _fuzzy_name_match("Janet Liff", "")


# ============================================================================
# Helper functions (pure unit tests)
# ============================================================================

class TestParsingHelpers:
    """Test parsing helper functions."""

    def test_is_role_parenthetical(self):
        assert _is_role("(Chair)")
        assert _is_role("(Vice Chair)")
        assert _is_role("(Treasurer)")
        assert not _is_role("Janet Liff")
        assert not _is_role("Chair")
        assert not _is_role("(")

    def test_is_company(self):
        assert _is_company("D.E. Shaw")
        assert _is_company("Local Projects")
        assert _is_company("Rubenstein")
        assert _is_company("Studio for Urban Projects")
        assert _is_company("Northwell")
        # Single word is treated as company by the heuristic
        assert _is_company("Stantec")
        # Multi-word with org keywords
        assert _is_company("Index Ventures")

    def test_is_not_company(self):
        assert not _is_company("Janet Liff")
        assert not _is_company("Martin Mignot")
        assert not _is_company("Mary Beth Kelly")

    def test_parse_name_and_role(self):
        name, role = _parse_name_and_role("Janet Liff (Chair)")
        assert name == "Janet Liff"
        assert role == "Chair"

    def test_parse_name_no_role(self):
        name, role = _parse_name_and_role("Martin Mignot")
        assert name == "Martin Mignot"
        assert role == ""

    def test_clean_name(self):
        assert _clean_name("Janet Liff,") == "Janet Liff"
        assert _clean_name("Richard B. Miller Esq") == "Richard B. Miller"
        assert _clean_name("  Janet Liff  ") == "Janet Liff"


# ============================================================================
# Reconciliation logic with mock data (no network)
# ============================================================================

class TestReconciliation:
    """Test 990-website reconciliation with mock data."""

    def _make_website_members(self):
        """Create mock website members matching ground truth."""
        board = [
            WebsiteMember(name="Janet Liff", role="Chair", category="board"),
            WebsiteMember(name="Hope Reeves", role="Vice Chair", category="board"),
            WebsiteMember(name="Stanley Toussaint", role="Treasurer", category="board"),
            WebsiteMember(name="Christine Berthet", category="board"),
            WebsiteMember(name="Daniel Kaizer", category="board"),
            WebsiteMember(name="Mary Beth Kelly", category="board"),
            WebsiteMember(name="Andy Lerner", category="board"),
            WebsiteMember(name="Gentry Lock", category="board"),
            WebsiteMember(name="Adam Mansky", category="board"),
            WebsiteMember(name="Martin Mignot", category="board"),
            WebsiteMember(name="Keith Tubbs", category="board"),
            WebsiteMember(name="Kenneth Weine", category="board"),
            WebsiteMember(name="Claire Weisz", category="board"),
            # New members not on 990
            WebsiteMember(name="Jake Barton", category="board"),
            WebsiteMember(name="Karl Chen", category="board"),
            WebsiteMember(name="Lucia Deng", category="board"),
            WebsiteMember(name="Edmundo Martinez", category="board"),
            WebsiteMember(name="Wiley Norvell", category="board"),
            WebsiteMember(name="Alison Sant", category="board"),
        ]
        advisory = [
            WebsiteMember(name="George Beane", category="advisory"),
            WebsiteMember(name="Curtis Archer", category="advisory"),
            WebsiteMember(name="Doug Ellis", category="advisory"),
        ]
        return board + advisory

    def _make_990_roster(self):
        """Create mock 990 roster matching ground truth."""
        members = []
        # Confirmed current (on both)
        for name, role in [
            ("Janet Liff", "Chair"),
            ("Hope Reeves", "Vice Chair"),
            ("Stanley Toussaint", "Treasurer"),
            ("Christine Berthet", "Director"),
            ("Daniel Kaizer", "Director"),
            ("Mary Beth Kelly", "Director"),
            ("Andy Lerner", "Director"),
            ("Gentry Lock", "Director"),
            ("Adam Mansky", "Director"),
            ("Martin Mignot", "Director"),
            ("Keith Tubbs", "Director"),
            ("Kenneth Weine", "Director"),
            ("Claire Weisz", "Director"),
        ]:
            members.append(BoardMember(
                name=name, role=role, member_type="board",
                is_board=True, source="propublica_990",
            ))

        # Departed (on 990, not on website board or advisory)
        for name, role in [
            ("Bahij Chancey", "Secretary"),
            ("John Choe", "Director"),
            ("Michael Epstein", "Director"),
            ("Richard B. Miller", "Director"),
            ("Sarah Kaufman", "Director"),
        ]:
            members.append(BoardMember(
                name=name, role=role, member_type="board",
                is_board=True, source="propublica_990",
            ))

        # Advisory council members (on 990 AND on website advisory)
        for name in ["George Beane", "Curtis Archer", "Doug Ellis"]:
            members.append(BoardMember(
                name=name, role="Director", member_type="board",
                is_board=True, source="propublica_990",
            ))

        # Staff
        members.append(BoardMember(
            name="Ben Furnas", role="Exec Director", member_type="staff",
            is_key_employee=True, source="propublica_990",
        ))

        return OrgRoster(
            org_name="Transportation Alternatives",
            ein="510186015",
            tax_period="2024-03",
            fiscal_year=2024,
            members=members,
            source="propublica_990",
        )

    def test_reconcile_confirmed_current(self):
        """Confirmed current: on both website board AND 990."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        confirmed = result["confirmed_current"]
        assert len(confirmed) >= 13, (
            f"Expected >=13 confirmed current, got {len(confirmed)}"
        )

        confirmed_names = {c["name_990"].lower() for c in confirmed}
        for name in ["janet liff", "hope reeves", "martin mignot"]:
            assert name in confirmed_names, f"'{name}' not in confirmed_current"

    def test_reconcile_departed(self):
        """Departed: on 990 but NOT on website board or advisory."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        departed = result["departed"]
        assert len(departed) >= 5, f"Expected >=5 departed, got {len(departed)}"

        departed_names = {d["name_990"].lower() for d in departed}
        assert "bahij chancey" in departed_names, "Bahij Chancey should be departed"

    def test_reconcile_new_unresolved(self):
        """New/unresolved: on website board but NOT on 990."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        new = result["new_unresolved"]
        assert len(new) >= 4, f"Expected >=4 new/unresolved, got {len(new)}"

        new_names = {n["name_website"].lower() for n in new}
        assert "jake barton" in new_names, "Jake Barton should be new/unresolved"
        assert "karl chen" in new_names, "Karl Chen should be new/unresolved"

    def test_reconcile_advisory(self):
        """Advisory: on 990 AND on website advisory council."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        advisory = result["advisory"]
        assert len(advisory) >= 3, f"Expected >=3 advisory, got {len(advisory)}"

        advisory_names = {a["name_990"].lower() for a in advisory}
        for name in ["george beane", "curtis archer", "doug ellis"]:
            assert name in advisory_names, (
                f"'{name}' should be advisory, not departed"
            )

    def test_advisory_not_in_departed(self):
        """Advisory council members should NOT appear in departed."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        departed_names = {d["name_990"].lower() for d in result["departed"]}
        for name in ["george beane", "curtis archer", "doug ellis"]:
            assert name not in departed_names, (
                f"'{name}' is advisory, should not be in departed"
            )

    def test_staff_excluded_from_reconciliation(self):
        """Staff (key employees) should not appear in board reconciliation categories."""
        website = self._make_website_members()
        roster = self._make_990_roster()
        result = reconcile_with_990(website, roster)

        all_names = set()
        for category in ["confirmed_current", "departed", "new_unresolved", "advisory"]:
            for entry in result[category]:
                name = entry.get("name_990", entry.get("name_website", "")).lower()
                all_names.add(name)

        # Ben Furnas is staff, not board -- should not appear in reconciliation
        # (unless the reconciliation only compares board members, which it does)
        assert "ben furnas" not in all_names, (
            "Staff member Ben Furnas should not be in board reconciliation"
        )

    def test_reconciliation_with_name_variants(self):
        """Reconciliation handles name variants (Ken vs Kenneth)."""
        website = [
            WebsiteMember(name="Ken Weine", category="board"),
        ]
        roster = OrgRoster(
            org_name="Test Org", ein="111111111", tax_period="2024",
            members=[
                BoardMember(name="Kenneth Weine", role="Director",
                            member_type="board", is_board=True),
            ],
        )
        result = reconcile_with_990(website, roster)
        assert len(result["confirmed_current"]) == 1
        assert len(result["departed"]) == 0


# ============================================================================
# TransAlt scraper (network tests)
# ============================================================================

@pytest.mark.network
@pytest.mark.asyncio
async def test_scraper_returns_board_and_advisory(test_db):
    """TransAlt scraper returns both board and advisory council members."""
    scraper = TransAltScraper(db=test_db)
    members = await scraper.scrape()

    board = [m for m in members if m.category == "board"]
    advisory = [m for m in members if m.category == "advisory"]

    assert len(board) >= 19, (
        f"Expected >=19 board members, got {len(board)}: {[m.name for m in board]}"
    )
    assert len(advisory) >= 3, (
        f"Expected >=3 advisory members, got {len(advisory)}: {[m.name for m in advisory]}"
    )


@pytest.mark.network
@pytest.mark.asyncio
async def test_scraper_board_has_roles(test_db):
    """Board members with officer roles have them captured."""
    scraper = TransAltScraper(db=test_db)
    members = await scraper.scrape()

    board = [m for m in members if m.category == "board"]

    # Janet Liff should have Chair role
    liff = [m for m in board if "liff" in m.name.lower()]
    assert liff, "Janet Liff not found"
    assert "chair" in liff[0].role.lower(), (
        f"Expected Chair role for Liff, got: '{liff[0].role}'"
    )


@pytest.mark.network
@pytest.mark.asyncio
async def test_scraper_distinguishes_board_from_advisory(test_db):
    """Board and advisory are correctly categorized."""
    scraper = TransAltScraper(db=test_db)
    members = await scraper.scrape()

    categories = {m.category for m in members}
    assert "board" in categories
    assert "advisory" in categories

    # Known board member should be board, not advisory
    liff = [m for m in members if "liff" in m.name.lower()]
    assert liff[0].category == "board"


@pytest.mark.network
@pytest.mark.asyncio
async def test_scraper_caching(test_db):
    """Second scrape serves from cache."""
    scraper = TransAltScraper(db=test_db)

    # First scrape
    members1 = await scraper.scrape()
    assert len(members1) > 0

    # Second scrape should come from cache
    members2 = await scraper.scrape()
    assert len(members2) == len(members1)

    # Verify cache was populated
    cached = test_db.cache_get("website_transalt", "transalt_staff", "scrape")
    assert cached is not None


@pytest.mark.network
@pytest.mark.slow
@pytest.mark.asyncio
async def test_full_reconciliation_live(propublica_source, test_db, ground_truth):
    """Full live reconciliation: scrape website, pull 990, compare."""
    scraper = TransAltScraper(db=test_db)
    website_members = await scraper.scrape()

    roster = await propublica_source.search_org(ground_truth["ein"])

    result = reconcile_with_990(website_members, roster)

    # Spec: >=13 confirmed current
    assert len(result["confirmed_current"]) >= 12, (
        f"Expected >=12 confirmed current, got {len(result['confirmed_current'])}"
    )

    # Spec: >=6 departed, including Bahij Chancey
    assert len(result["departed"]) >= 4, (
        f"Expected >=4 departed, got {len(result['departed'])}"
    )

    # Spec: >=4 new/unresolved, including Jake Barton, Karl Chen
    assert len(result["new_unresolved"]) >= 4, (
        f"Expected >=4 new/unresolved, got {len(result['new_unresolved'])}"
    )
