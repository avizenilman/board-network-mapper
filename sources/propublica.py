"""ProPublica Nonprofit Explorer — 990 data source.

People search: HTML scrape of /nonprofits/name_search?q={name}
Org data: API at /nonprofits/api/v2/organizations/{ein}.json
Officer list: HTML scrape of /nonprofits/organizations/{ein}
Per-filing officers: HTML scrape of /nonprofits/organizations/{ein}/{object_id}
"""

import asyncio
import re
import ssl
import aiohttp
from bs4 import BeautifulSoup
from typing import Optional

from sources.base import DataSource
from core.models import BoardSeat, BoardMember, OrgRoster
from core.database import Database

BASE_URL = "https://projects.propublica.org"
MAX_CONCURRENT = 5
REQUEST_DELAY = 0.5  # seconds between requests

# macOS Python often lacks system certs; fall back to unverified
_SSL_CONTEXT = ssl.create_default_context()
try:
    import certifi
    _SSL_CONTEXT.load_verify_locations(certifi.where())
except (ImportError, Exception):
    _SSL_CONTEXT = ssl.create_default_context()
    _SSL_CONTEXT.check_hostname = False
    _SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _classify_role(role_str: str) -> str:
    """Classify a role string into member_type."""
    role_lower = role_str.lower().strip()
    if not role_lower:
        return "board"
    # Staff roles — check these first (more specific patterns)
    staff_patterns = [
        "executive director", "deputy director", "development director",
        "director of", "senior director", "managing director",
        "chief", "ceo", "cfo", "coo", "cto",
        "president" if "vice president" not in role_lower else None,
        "vp of", "vice president of",
        "counsel", "coordinator", "associate director",
        "advisor", "analyst", "manager",
        "officer" if "chief" in role_lower else None,
        "interim co executive", "co-executive", "co executive",
        "operations officer",
    ]
    for pattern in staff_patterns:
        if pattern and pattern in role_lower:
            return "staff"
    # Board roles
    board_patterns = [
        "chair", "vice chair", "treasurer", "secretary",
        "member", "trustee", "board member", "director",
    ]
    for pattern in board_patterns:
        if pattern in role_lower:
            return "board"
    return "board"


def _clean_name(name: str) -> str:
    """Remove date suffixes and junk from names."""
    # Remove "Until XXX", "Start XXX", "Thru XXX" etc
    name = re.sub(r'\s+(Until|Start|Thru|Through|From)\s+\d+[/\d]*.*$', '', name, flags=re.IGNORECASE)
    # Remove trailing date patterns like "424" (4/24)
    name = re.sub(r'\s+\d{3,4}\s*$', '', name)
    # Remove leading/trailing junk
    name = re.sub(r'[)\s]+$', '', name)
    name = re.sub(r'^[(\s]+', '', name)
    return name.strip()


def _parse_compensation(text: str) -> float:
    """Parse a compensation string like '$182,812' into a float."""
    text = text.strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except (ValueError, TypeError):
        return 0.0


def _parse_tax_period(text: str) -> tuple[str, int]:
    """Parse tax period text. Returns (period_str, fiscal_year)."""
    # "2025" -> "2025", 2025
    # "202303" -> "2023-03", 2023
    text = text.strip()
    if len(text) == 4:
        return text, int(text)
    if len(text) == 6:
        return f"{text[:4]}-{text[4:]}", int(text[:4])
    # Try to extract year
    match = re.search(r'(\d{4})', text)
    if match:
        yr = int(match.group(1))
        return text, yr
    return text, 0


class ProPublicaSource(DataSource):
    name = "propublica_990"

    def __init__(self, db: Database = None):
        self.db = db
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        async with self._semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            async with session.get(url, ssl=_SSL_CONTEXT) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> dict:
        async with self._semaphore:
            await asyncio.sleep(REQUEST_DELAY)
            async with session.get(url, ssl=_SSL_CONTEXT) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def search_person(self, name: str) -> list[BoardSeat]:
        """Search for a person across all 990 filings."""
        # Check cache
        if self.db:
            cached = self.db.cache_get(self.name, name, "person")
            if cached:
                return [BoardSeat(**s) for s in cached]

        seats = []
        async with aiohttp.ClientSession() as session:
            url = f"{BASE_URL}/nonprofits/name_search?q={name.replace(' ', '+')}"
            try:
                html = await self._fetch(session, url)
            except Exception:
                return seats

            soup = BeautifulSoup(html, "lxml")
            result_rows = soup.select(".result-row-people")

            for row in result_rows:
                seat = self._parse_person_result(row)
                if seat:
                    seats.append(seat)

            # Check for additional pages
            next_link = soup.select_one("a.next_page")
            page = 2
            while next_link and page <= 10:
                try:
                    next_url = BASE_URL + next_link["href"]
                    html = await self._fetch(session, next_url)
                    soup = BeautifulSoup(html, "lxml")
                    for row in soup.select(".result-row-people"):
                        seat = self._parse_person_result(row)
                        if seat:
                            seats.append(seat)
                    next_link = soup.select_one("a.next_page")
                    page += 1
                except Exception:
                    break

        # Cache results
        if self.db and seats:
            self.db.cache_set(self.name, name, "person", [s.model_dump() for s in seats])

        return seats

    def _parse_person_result(self, row) -> Optional[BoardSeat]:
        """Parse a single person search result row."""
        name_el = row.select_one(".result-item__hed")
        if not name_el:
            return None
        person_name = name_el.get_text(strip=True)

        # Role and org
        link_el = row.select_one(".text-link a")
        org_name = link_el.get_text(strip=True) if link_el else ""
        ein = ""
        if link_el and link_el.get("href"):
            ein_match = re.search(r'/organizations/(\d+)', link_el["href"])
            if ein_match:
                ein = ein_match.group(1)

        role_span = row.select_one(".text-link .margin-right")
        role_text = ""
        if role_span:
            # get_text with separator to handle embedded newlines
            raw = role_span.get_text(separator=" ", strip=True)
            # Normalize whitespace
            raw = re.sub(r'\s+', ' ', raw).strip()
            role_text = raw
        # Extract just the role (before "at OrgName")
        # Pattern: "Chair at Transportation Alternatives Inc" -> "Chair"
        role_match = re.match(r'^(.+?)\s+at\s+', role_text, re.IGNORECASE)
        role = role_match.group(1).strip() if role_match else role_text

        # Location and year from the "nowrap text-sub" span
        sub_span = row.select_one(".text-link .nowrap.text-sub")
        location = ""
        year = ""
        if sub_span:
            raw = sub_span.get_text(separator=" ", strip=True)
            raw = re.sub(r'\s+', ' ', raw).strip()
            # "New York, NY • 2025"
            parts = raw.split("•")
            if len(parts) >= 1:
                location = parts[0].strip()
            if len(parts) >= 2:
                year = parts[1].strip()

        # Compensation
        comp_divs = row.select(".comp-wrapper .amount")
        comp = _parse_compensation(comp_divs[0].get_text()) if len(comp_divs) > 0 else 0.0
        comp_related = _parse_compensation(comp_divs[1].get_text()) if len(comp_divs) > 1 else 0.0
        comp_other = _parse_compensation(comp_divs[2].get_text()) if len(comp_divs) > 2 else 0.0

        # Parse location
        city, state = "", ""
        if "," in location:
            city, state = location.rsplit(",", 1)
            city, state = city.strip(), state.strip()

        tax_period, fiscal_year = _parse_tax_period(year)
        member_type = _classify_role(role)

        return BoardSeat(
            person_name=person_name,
            org_name=org_name,
            ein=ein,
            role=role,
            member_type=member_type,
            tax_period=tax_period,
            fiscal_year=fiscal_year,
            compensation=comp,
            compensation_related=comp_related,
            compensation_other=comp_other,
            source=self.name,
            is_current=True,  # refined later with historical comparison
            city=city,
            state=state,
        )

    async def search_org(self, ein: str) -> OrgRoster:
        """Get all officers/directors from an org's most recent filing."""
        # Check cache
        if self.db:
            cached = self.db.cache_get(self.name, ein, "org")
            if cached:
                return OrgRoster(**cached)

        roster = OrgRoster(org_name="", ein=ein, tax_period="", source=self.name)

        async with aiohttp.ClientSession() as session:
            # Get org metadata from API
            api_url = f"{BASE_URL}/nonprofits/api/v2/organizations/{ein}.json"
            try:
                api_data = await self._fetch_json(session, api_url)
                org_info = api_data.get("organization", {})
                roster.org_name = org_info.get("name", "")
                roster.city = org_info.get("city", "")
                roster.state = org_info.get("state", "")
                tax_prd = str(org_info.get("tax_period", ""))
                roster.tax_period = tax_prd[:7] if len(tax_prd) >= 7 else tax_prd
                if org_info.get("foundation_code") in (2, 4):
                    roster.org_type = "private_foundation"

                # Get filings to find object IDs
                filings = api_data.get("filings_with_data", [])
            except Exception:
                filings = []

            # Scrape org page for officer list
            org_url = f"{BASE_URL}/nonprofits/organizations/{ein}"
            try:
                html = await self._fetch(session, org_url)
                roster.members = self._parse_org_officers(html)

                # Also try to get filing-specific pages for more complete data
                if filings and not roster.members:
                    for filing in filings[:1]:
                        obj_id = filing.get("formtype_str", "")
                        # Try the full filing page
                        pass  # org page usually has the data
            except Exception:
                pass

            # Set fiscal year from tax period
            if roster.tax_period:
                match = re.search(r'(\d{4})', roster.tax_period)
                if match:
                    roster.fiscal_year = int(match.group(1))

        # Cache results
        if self.db and roster.members:
            self.db.cache_set(self.name, ein, "org", roster.model_dump())

        return roster

    async def search_org_all_years(self, ein: str) -> list[OrgRoster]:
        """Get officer lists from multiple filing years.

        Uses the main org page which lists all years, then maps each section
        to the corresponding filing from the API.
        """
        if self.db:
            cached = self.db.cache_get(self.name, ein, "org_all_years")
            if cached:
                return [OrgRoster(**r) for r in cached]

        rosters = []
        async with aiohttp.ClientSession() as session:
            # Get filing list from API
            api_url = f"{BASE_URL}/nonprofits/api/v2/organizations/{ein}.json"
            try:
                api_data = await self._fetch_json(session, api_url)
                org_info = api_data.get("organization", {})
                filings = api_data.get("filings_with_data", [])
            except Exception:
                return rosters

            is_pf = org_info.get("foundation_code") in (2, 4)

            # Scrape the main org page which has all years
            org_url = f"{BASE_URL}/nonprofits/organizations/{ein}"
            try:
                html = await self._fetch(session, org_url)
                sections = self._parse_org_officers_all_years(html)

                # Map sections to filings by index (they appear in same order)
                for i, (label, members) in enumerate(sections):
                    if i < len(filings):
                        tp = filings[i].get("tax_prd", 0)
                        tp_str = str(tp)
                        tax_period = f"{tp_str[:4]}-{tp_str[4:]}" if len(tp_str) == 6 else tp_str
                        fy = filings[i].get("tax_prd_yr", 0)
                    else:
                        tax_period = label
                        fy = 0
                        match = re.search(r'(\d{4})', label)
                        if match:
                            fy = int(match.group(1))

                    roster = OrgRoster(
                        org_name=org_info.get("name", ""),
                        ein=ein,
                        tax_period=tax_period,
                        fiscal_year=fy,
                        members=members,
                        city=org_info.get("city", ""),
                        state=org_info.get("state", ""),
                        source=self.name,
                        org_type="private_foundation" if is_pf else "public_charity",
                    )
                    rosters.append(roster)
            except Exception:
                pass

        if self.db and rosters:
            self.db.cache_set(self.name, ein, "org_all_years",
                              [r.model_dump() for r in rosters])

        return rosters

    def _parse_org_page(self, html: str) -> list[tuple[str, list[BoardMember]]]:
        """Parse officers from a ProPublica org page, separated by filing year.

        Returns a list of (filing_label, members) tuples. The org page shows
        multiple fiscal years, each under its own section with a compensation table.
        """
        soup = BeautifulSoup(html, "lxml")
        filing_sections: list[tuple[str, list[BoardMember]]] = []

        # Each filing year is in a <section class="padded-box"> with a compensation table
        sections = soup.select("section.padded-box")
        for section in sections:
            table = section.select_one("table.employees")
            if not table:
                continue

            # Try to find the filing year label from a preceding header
            header = section.select_one(".table-header .table-header__hed")
            filing_label = header.get_text(strip=True) if header else ""

            members = self._parse_compensation_table(table)
            if members:
                filing_sections.append((filing_label, members))

        # If no sections found, try tables directly
        if not filing_sections:
            tables = soup.select("table.employees")
            for table in tables:
                members = self._parse_compensation_table(table)
                if members:
                    filing_sections.append(("", members))

        return filing_sections

    def _parse_compensation_table(self, table) -> list[BoardMember]:
        """Parse a single compensation table into BoardMember list."""
        members = []
        rows = table.select("tr.employee-row")
        for row in rows:
            td = row.select("td")
            if not td:
                continue

            name_td = td[0]
            name_text = name_td.get_text(strip=True)

            # Skip "See filing for compensation of X other people"
            if "see filing" in name_text.lower() or "filing for compensation" in name_text.lower():
                continue

            # Name is before the span, role is in the span
            span = name_td.select_one("span")
            role = ""
            if span:
                role = span.get_text(strip=True).strip("()")
                # Remove role from name text
                name = name_text.replace(f"({role})", "").strip()
            else:
                name = name_text

            # Clean the name
            name = _clean_name(name)
            if not name or len(name) < 2:
                continue

            # Compensation
            comp = _parse_compensation(td[1].get_text()) if len(td) > 1 else 0.0
            comp_related = _parse_compensation(td[2].get_text()) if len(td) > 2 else 0.0
            comp_other = _parse_compensation(td[3].get_text()) if len(td) > 3 else 0.0

            member_type = _classify_role(role)

            # Determine 990 Part VII checkboxes from role
            role_lower = role.lower()
            is_board = member_type == "board"
            is_officer = any(kw in role_lower for kw in [
                "chair", "treasurer", "secretary", "president", "vice chair"
            ])
            is_key_employee = member_type == "staff"
            is_former = "former" in role_lower or "thru" in name_text.lower() or "until" in name_text.lower()

            members.append(BoardMember(
                name=name,
                role=role,
                member_type=member_type,
                is_board=is_board,
                is_officer=is_officer,
                is_key_employee=is_key_employee,
                is_former=is_former,
                compensation=comp,
                compensation_related=comp_related,
                compensation_other=comp_other,
                source=self.name,
            ))

        return members

    def _parse_org_officers(self, html: str) -> list[BoardMember]:
        """Parse officers from the most recent filing on a ProPublica org page."""
        sections = self._parse_org_page(html)
        if sections:
            # Return only the first (most recent) filing's officers
            return sections[0][1]
        return []

    def _parse_org_officers_all_years(self, html: str) -> list[tuple[str, list[BoardMember]]]:
        """Parse officers from all filing years on a ProPublica org page."""
        return self._parse_org_page(html)
