"""Transportation Alternatives website scraper.

Scrapes https://transalt.org/staff to extract:
- Board of Directors (section anchored by #board)
- Advisory Council (section anchored by #advisory)
- Staff (structured card layout)

The page is Squarespace-based. Board and advisory names are in
<br>-separated text blocks. Names are interleaved with company
affiliations and parenthetical roles.
"""

import asyncio
import re
import ssl
import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup, Tag

from core.models import BoardMember, OrgRoster, WebsiteMember
from core.database import Database

logger = logging.getLogger(__name__)

SOURCE_URL = "https://transalt.org/staff"

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Words that indicate a company/org, not a person name
_ORG_KEYWORDS = {
    "co.", "inc.", "inc", "llc", "llp", "corp", "partners", "group",
    "capital", "foundation", "associates", "ventures", "projects",
    "consulting", "consultant", "consultants", "studio", "agency",
    "practice", "psychotherapy", "company", "records", "media",
    "stantec", "rubenstein", "northwell", "mocafi",
}

# Known suffixes to strip from names
_NAME_SUFFIXES = {"acsw", "lcsw", "esq", "phd", "md", "jr", "sr", "ii", "iii"}


def _is_role(text: str) -> bool:
    """Check if a line is a parenthetical role like '(Chair)'."""
    return bool(re.match(r'^\(.+\)$', text.strip()))


def _is_company(text: str) -> bool:
    """Check if a line is a company/org affiliation, not a person name."""
    text = text.strip()
    if not text or len(text) < 2:
        return True  # junk

    lower = text.lower()

    # Single punctuation or very short
    if len(text) <= 2 and not text[0].isalpha():
        return True

    # Contains org keywords
    words = lower.split()
    for word in words:
        word_clean = word.strip(".,;:")
        if word_clean in _ORG_KEYWORDS:
            return True

    # All caps (like "CHECKPEDS") — likely an org acronym
    if text.isupper() and len(text) > 2:
        return True

    # Single word that isn't a common name pattern
    if len(words) == 1:
        # Could be a company name on its own line
        return True

    # Contains "&" or "+" (like "Capalino + Company")
    if "&" in text or "+" in text:
        return True

    # Starts with "D.E." or similar initials pattern followed by a word
    if re.match(r'^[A-Z]\.[A-Z]\.?\s', text):
        return True

    # "J. Liff Co." pattern
    if re.match(r'^[A-Z]\.\s', text) and any(w in lower for w in _ORG_KEYWORDS):
        return True

    # Private practice, etc.
    if "private" in lower and "practice" in lower:
        return True

    # Contains comma followed by organization-like text
    if "," in text:
        parts = text.split(",")
        # "CHECKPEDS , Sunnyside Records" — both company names
        if all(_is_company(p.strip()) or len(p.strip()) < 3 for p in parts):
            return True

    return False


def _clean_name(name: str) -> str:
    """Clean a name string — remove suffixes, normalize whitespace."""
    # Remove known suffixes
    words = name.split()
    cleaned = []
    for w in words:
        if w.lower().strip(".,") not in _NAME_SUFFIXES:
            cleaned.append(w)
    name = " ".join(cleaned)
    # Remove trailing comma
    name = name.rstrip(",").strip()
    return name


def _parse_name_and_role(text: str) -> tuple[str, str]:
    """Extract name and parenthetical role from text like 'Janet Liff (Chair)'."""
    match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', text.strip())
    if match:
        return _clean_name(match.group(1).strip()), match.group(2).strip()
    return _clean_name(text.strip()), ""


def _extract_br_lines(element: Tag) -> list[str]:
    """Extract text lines from an element, splitting on <br> tags."""
    lines = []
    current = []
    for child in element.descendants:
        if isinstance(child, str):
            text = child.strip()
            if text:
                current.append(text)
        elif hasattr(child, 'name'):
            if child.name == "br":
                if current:
                    lines.append(" ".join(current))
                    current = []
            elif child.name in ("script", "style"):
                continue
    if current:
        lines.append(" ".join(current))
    return [line.strip() for line in lines if line.strip()]


def _extract_p_entries(block: Tag) -> list[list[str]]:
    """Extract person entries from a block, one entry per <p> tag.

    Each entry is a list of lines (split on <br>) within that <p>.
    This preserves the boundary between people, which are separated
    by <p> tags on the TransAlt Squarespace page.
    """
    entries = []
    for p in block.find_all("p", recursive=True):
        # Skip empty placeholder paragraphs
        if p.get("data-rte-preserve-empty"):
            continue
        text = p.get_text(strip=True)
        if not text:
            continue
        # Split on <br> within this <p>
        lines = _extract_br_lines(p)
        if lines:
            entries.append(lines)
    return entries


def _fuzzy_name_match(a: str, b: str) -> bool:
    """Check if two names likely refer to the same person."""
    a_norm = _clean_name(a).lower().strip()
    b_norm = _clean_name(b).lower().strip()
    if a_norm == b_norm:
        return True
    a_parts = a_norm.split()
    b_parts = b_norm.split()
    if not a_parts or not b_parts:
        return False
    # Last name must match
    if a_parts[-1] != b_parts[-1]:
        return False
    # First name match or prefix match (Ken/Kenneth)
    if a_parts[0] == b_parts[0]:
        return True
    if a_parts[0].startswith(b_parts[0]) or b_parts[0].startswith(a_parts[0]):
        return True
    # Middle name/initial tolerance (George H Beane vs George Beane)
    if len(a_parts) != len(b_parts):
        shorter = a_parts if len(a_parts) < len(b_parts) else b_parts
        longer = b_parts if len(a_parts) < len(b_parts) else a_parts
        if shorter[0] == longer[0] and shorter[-1] == longer[-1]:
            return True
    return False


class TransAltScraper:
    """Scraper for Transportation Alternatives staff/board page."""

    def __init__(self, db: Database = None):
        self.db = db

    async def _fetch(self, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, ssl=_SSL_CONTEXT) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def scrape(self, force_refresh: bool = False) -> list[WebsiteMember]:
        """Scrape the TransAlt staff page."""
        if self.db and not force_refresh:
            cached = self.db.cache_get("website_transalt", "transalt_staff", "scrape")
            if cached:
                return [WebsiteMember(**m) for m in cached]

        html = await self._fetch(SOURCE_URL)
        members = self._parse_page(html)

        if self.db and members:
            self.db.cache_set("website_transalt", "transalt_staff", "scrape",
                              [m.model_dump() for m in members])
        return members

    def _parse_page(self, html: str) -> list[WebsiteMember]:
        soup = BeautifulSoup(html, "html.parser")
        members = []
        members.extend(self._parse_named_section(soup, "board"))
        members.extend(self._parse_named_section(soup, "advisory"))
        members.extend(self._parse_staff(soup))
        return members

    def _parse_named_section(self, soup: BeautifulSoup, anchor_id: str) -> list[WebsiteMember]:
        """Parse board or advisory section by finding the #anchor div.

        The page uses <p> tags to separate people. Within each <p>, the
        first line (before any <br>) is the person's name; subsequent
        lines are roles like '(Chair)' or company affiliations.
        """
        category = "board" if anchor_id == "board" else "advisory"
        members = []

        # Find the anchor div
        anchor = soup.find(id=anchor_id)
        if not anchor:
            logger.warning("Could not find #%s anchor", anchor_id)
            return members

        # Walk up to the section element
        section = anchor
        for _ in range(15):
            section = section.parent
            if section is None:
                break
            if section.name == "section":
                break

        if section is None or section.name != "section":
            logger.warning("Could not find section for #%s", anchor_id)
            return members

        # Collect person entries from all text blocks in this section.
        # Each <p> tag is one person; lines within it split on <br>.
        for block in section.find_all("div", class_="sqs-html-content"):
            entries = _extract_p_entries(block)
            for lines in entries:
                if not lines:
                    continue

                first_line = lines[0].strip()

                # Skip section headings and descriptive paragraphs
                if first_line.lower() in ("board of directors", "advisory council", "staff"):
                    continue
                if len(first_line) > 100:
                    continue

                # The first line is the name (possibly with inline role)
                name, role = _parse_name_and_role(first_line)

                # Check remaining lines for a parenthetical role
                if not role:
                    for extra_line in lines[1:]:
                        if _is_role(extra_line):
                            role = extra_line.strip("()")
                            break

                name = _clean_name(name)
                if not name or len(name) < 2:
                    continue

                # Skip if this looks like a company, not a person
                # But be lenient: allow single-word names that are
                # clearly names (capitalized, no org keywords)
                if _is_company(name) and len(name.split()) < 2:
                    continue

                members.append(WebsiteMember(
                    name=name, role=role,
                    category=category, title=role,
                    source_url=SOURCE_URL,
                ))

        return members

    def _parse_staff(self, soup: BeautifulSoup) -> list[WebsiteMember]:
        """Parse staff from structured card elements."""
        members = []
        items = soup.select(".user-items-list-item-container")
        for item in items:
            name_el = item.select_one(".list-item-content__title")
            title_el = item.select_one(".list-item-content__description")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            title = title_el.get_text(strip=True) if title_el else ""
            if name and len(name) >= 2:
                members.append(WebsiteMember(
                    name=name, role=title, category="staff",
                    title=title, source_url=SOURCE_URL,
                ))
        return members


def reconcile_with_990(
    website_members: list[WebsiteMember],
    roster_990: OrgRoster,
) -> dict[str, list]:
    """Compare website data with 990 filing data.

    Returns:
        confirmed_current: on both website (board) and 990
        departed: on 990 but not on website
        new_unresolved: on website (board) but not on 990
        advisory: on 990 AND on website advisory council
    """
    website_board = [m for m in website_members if m.category == "board"]
    website_advisory = [m for m in website_members if m.category == "advisory"]

    roster_board = [
        m for m in roster_990.members
        if m.member_type in ("board", "officer") or m.is_board
    ]

    confirmed_current = []
    departed = []
    new_unresolved = []
    advisory = []

    matched_website_idx = set()

    for rm in roster_board:
        board_match_idx = None
        advisory_match = None

        for i, wm in enumerate(website_board):
            if _fuzzy_name_match(rm.name, wm.name):
                board_match_idx = i
                matched_website_idx.add(i)
                break

        if board_match_idx is None:
            for wm in website_advisory:
                if _fuzzy_name_match(rm.name, wm.name):
                    advisory_match = wm
                    break

        if board_match_idx is not None:
            wm = website_board[board_match_idx]
            confirmed_current.append({
                "name_990": rm.name, "name_website": wm.name,
                "role_990": rm.role, "role_website": wm.role,
            })
        elif advisory_match is not None:
            advisory.append({
                "name_990": rm.name, "name_website": advisory_match.name,
                "role_990": rm.role, "role_advisory": advisory_match.role,
            })
        else:
            departed.append({"name_990": rm.name, "role_990": rm.role})

    for i, wm in enumerate(website_board):
        if i not in matched_website_idx:
            new_unresolved.append({
                "name_website": wm.name, "role_website": wm.role,
            })

    return {
        "confirmed_current": confirmed_current,
        "departed": departed,
        "new_unresolved": new_unresolved,
        "advisory": advisory,
    }
