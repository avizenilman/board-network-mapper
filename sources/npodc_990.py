"""NPODC/NCCS IRS 990 Part VII compensation data source.

Local SQLite-backed source — no network calls at query time.
Data loaded by scripts/load_npodc.py from NCCS Efile CSV downloads.

Table: npodc_compensation in data/board_mapper.db
"""

import re
import sqlite3
from typing import Optional

from sources.base import DataSource
from core.models import BoardSeat, BoardMember, OrgRoster
from core.database import Database, DEFAULT_DB_PATH


def _classify_role(title: str, is_dir: bool, is_off: bool,
                   is_key: bool, is_hc: bool) -> str:
    """Determine member_type from 990 Part VII flags and title."""
    if is_key or is_hc:
        return "staff"
    if is_dir or is_off:
        return "board"
    # Fall back to title-based classification
    title_lower = (title or "").lower().strip()
    staff_keywords = [
        "executive director", "deputy director", "director of",
        "chief", "ceo", "cfo", "coo", "cto", "president",
        "vp ", "vice president", "counsel", "manager",
        "coordinator", "analyst",
    ]
    for kw in staff_keywords:
        if kw in title_lower:
            return "staff"
    return "board"


def _clean_title(raw: str) -> str:
    """Normalize a title for display."""
    if not raw:
        return ""
    # Trim and title-case if ALL CAPS
    s = raw.strip()
    if s == s.upper() and len(s) > 3:
        s = s.title()
    return s


def _parse_tax_period(begin_date: str, end_date: str, tax_year: int) -> str:
    """Build a tax period string like '2024-12' from available fields."""
    # Try to extract from end date (e.g., "2024-12-31" -> "2024-12")
    if end_date:
        match = re.match(r'(\d{4})-(\d{2})', end_date)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
    if tax_year:
        return str(tax_year)
    return ""


def _split_name(full_name: str) -> tuple[str, str]:
    """Split name into (first, last). Handles 'LAST, FIRST' and 'FIRST LAST'."""
    if not full_name:
        return ("", "")
    name = full_name.strip()
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ""
        return (first, last)
    parts = name.split()
    if len(parts) <= 1:
        return ("", name)
    return (parts[0], " ".join(parts[1:]))


class NPODCSource(DataSource):
    """IRS 990 Part VII compensation data from NCCS/NPODC bulk files.

    This source queries a local SQLite table (npodc_compensation) directly.
    No network calls — all data is pre-loaded by scripts/load_npodc.py.
    """
    name = "npodc_990"

    def __init__(self, db: Database = None, db_path: str = None):
        self.db = db
        # Direct SQLite connection for NPODC queries
        # Use the same DB file as the Database class
        if db_path:
            self._db_path = db_path
        elif db:
            self._db_path = db.db_path
        else:
            self._db_path = DEFAULT_DB_PATH
        self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy connection to SQLite."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _table_exists(self) -> bool:
        """Check if the npodc_compensation table exists."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='npodc_compensation'"
        ).fetchone()
        return row is not None

    def _row_to_board_seat(self, row: sqlite3.Row) -> BoardSeat:
        """Convert a DB row to a BoardSeat model."""
        is_dir = bool(row["is_trustee_director"])
        is_off = bool(row["is_officer"])
        is_key = bool(row["is_key_employee"])
        is_hc = bool(row["is_highest_compensated"])
        is_former = bool(row["is_former"])

        title = _clean_title(row["title"])
        member_type = _classify_role(title, is_dir, is_off, is_key, is_hc)

        # Build a readable role string from title and flags
        role = title if title else self._flags_to_role(is_dir, is_off, is_key, is_hc, is_former)

        tax_period = _parse_tax_period(
            row["tax_period_begin"], row["tax_period_end"], row["tax_year"]
        )

        # Determine org_type from return_type
        return_type = (row["return_type"] or "").strip()
        if "PF" in return_type or return_type == "990PF":
            org_type = "private_foundation"
        else:
            org_type = "public_charity"

        # Display name: title-case if all caps
        person_name = row["person_name"] or ""
        if person_name == person_name.upper() and len(person_name) > 3:
            person_name = person_name.title()

        org_name = row["org_name"] or ""
        if org_name == org_name.upper() and len(org_name) > 3:
            org_name = org_name.title()

        return BoardSeat(
            person_name=person_name,
            org_name=org_name,
            ein=row["ein"],
            role=role,
            member_type=member_type,
            tax_period=tax_period,
            fiscal_year=row["tax_year"] or 0,
            compensation=float(row["comp_org"] or 0),
            compensation_related=float(row["comp_related"] or 0),
            compensation_other=float(row["comp_other"] or 0),
            source=self.name,
            is_current=not is_former,
            org_type=org_type,
        )

    def _row_to_board_member(self, row: sqlite3.Row) -> BoardMember:
        """Convert a DB row to a BoardMember model."""
        is_dir = bool(row["is_trustee_director"])
        is_off = bool(row["is_officer"])
        is_key = bool(row["is_key_employee"])
        is_hc = bool(row["is_highest_compensated"])
        is_former = bool(row["is_former"])

        title = _clean_title(row["title"])
        member_type = _classify_role(title, is_dir, is_off, is_key, is_hc)
        role = title if title else self._flags_to_role(is_dir, is_off, is_key, is_hc, is_former)

        person_name = row["person_name"] or ""
        if person_name == person_name.upper() and len(person_name) > 3:
            person_name = person_name.title()

        return BoardMember(
            name=person_name,
            role=role,
            member_type=member_type,
            is_board=is_dir,
            is_officer=is_off,
            is_key_employee=is_key,
            is_highest_compensated=is_hc,
            is_former=is_former,
            compensation=float(row["comp_org"] or 0),
            compensation_related=float(row["comp_related"] or 0),
            compensation_other=float(row["comp_other"] or 0),
            source=self.name,
        )

    @staticmethod
    def _flags_to_role(is_dir: bool, is_off: bool, is_key: bool,
                       is_hc: bool, is_former: bool) -> str:
        """Generate a role description from Part VII flags."""
        parts = []
        if is_former:
            parts.append("Former")
        if is_dir:
            parts.append("Director/Trustee")
        if is_off:
            parts.append("Officer")
        if is_key:
            parts.append("Key Employee")
        if is_hc:
            parts.append("Highest Compensated")
        return ", ".join(parts) if parts else "Listed"

    async def search_person(self, name: str) -> list[BoardSeat]:
        """Find all 990 filings listing this person.

        Uses case-insensitive matching on last_name + first_name.
        Returns results across all years and organizations.
        """
        if not self._table_exists():
            return []

        conn = self._get_conn()
        first, last = _split_name(name)

        seats = []

        if first and last:
            # Primary: match on split name fields
            rows = conn.execute(
                """SELECT * FROM npodc_compensation
                   WHERE last_name LIKE ? AND first_name LIKE ?
                   ORDER BY tax_year DESC, comp_org DESC""",
                (f"{last}%", f"{first}%")
            ).fetchall()

            # If no results, try the reverse (in case name was "First Last")
            if not rows:
                rows = conn.execute(
                    """SELECT * FROM npodc_compensation
                       WHERE last_name LIKE ? AND first_name LIKE ?
                       ORDER BY tax_year DESC, comp_org DESC""",
                    (f"{first}%", f"{last}%")
                ).fetchall()
        else:
            # Single name: search both fields
            search_name = last or first or name
            rows = conn.execute(
                """SELECT * FROM npodc_compensation
                   WHERE last_name LIKE ? OR first_name LIKE ?
                   OR person_name LIKE ?
                   ORDER BY tax_year DESC, comp_org DESC
                   LIMIT 200""",
                (f"{search_name}%", f"{search_name}%", f"%{search_name}%")
            ).fetchall()

        for row in rows:
            seats.append(self._row_to_board_seat(row))

        return seats

    async def search_org(self, ein: str) -> OrgRoster:
        """Get the most recent filing's officer/director list for an org."""
        if not self._table_exists():
            return OrgRoster(org_name="", ein=ein, tax_period="", source=self.name)

        conn = self._get_conn()
        clean_ein = ein.replace("-", "").strip().zfill(9)

        # Get the most recent tax year for this EIN
        latest = conn.execute(
            "SELECT MAX(tax_year) FROM npodc_compensation WHERE ein = ?",
            (clean_ein,)
        ).fetchone()

        if not latest or not latest[0]:
            return OrgRoster(org_name="", ein=ein, tax_period="", source=self.name)

        tax_year = latest[0]

        rows = conn.execute(
            """SELECT * FROM npodc_compensation
               WHERE ein = ? AND tax_year = ?
               ORDER BY comp_org DESC""",
            (clean_ein, tax_year)
        ).fetchall()

        if not rows:
            return OrgRoster(org_name="", ein=ein, tax_period="", source=self.name)

        first_row = rows[0]
        org_name = first_row["org_name"] or ""
        if org_name == org_name.upper() and len(org_name) > 3:
            org_name = org_name.title()

        tax_period = _parse_tax_period(
            first_row["tax_period_begin"], first_row["tax_period_end"], tax_year
        )

        return_type = (first_row["return_type"] or "").strip()
        org_type = "private_foundation" if "PF" in return_type else "public_charity"

        members = [self._row_to_board_member(r) for r in rows]

        return OrgRoster(
            org_name=org_name,
            ein=clean_ein,
            tax_period=tax_period,
            fiscal_year=tax_year,
            members=members,
            org_type=org_type,
            source=self.name,
        )

    async def search_org_all_years(self, ein: str) -> list[OrgRoster]:
        """Get officer/director lists for all filing years of an org."""
        if not self._table_exists():
            return []

        conn = self._get_conn()
        clean_ein = ein.replace("-", "").strip().zfill(9)

        # Get all distinct tax years
        year_rows = conn.execute(
            "SELECT DISTINCT tax_year FROM npodc_compensation WHERE ein = ? ORDER BY tax_year DESC",
            (clean_ein,)
        ).fetchall()

        rosters = []
        for yr in year_rows:
            tax_year = yr[0]
            rows = conn.execute(
                """SELECT * FROM npodc_compensation
                   WHERE ein = ? AND tax_year = ?
                   ORDER BY comp_org DESC""",
                (clean_ein, tax_year)
            ).fetchall()

            if not rows:
                continue

            first_row = rows[0]
            org_name = first_row["org_name"] or ""
            if org_name == org_name.upper() and len(org_name) > 3:
                org_name = org_name.title()

            tax_period = _parse_tax_period(
                first_row["tax_period_begin"], first_row["tax_period_end"], tax_year
            )

            return_type = (first_row["return_type"] or "").strip()
            org_type = "private_foundation" if "PF" in return_type else "public_charity"

            members = [self._row_to_board_member(r) for r in rows]

            rosters.append(OrgRoster(
                org_name=org_name,
                ein=clean_ein,
                tax_period=tax_period,
                fiscal_year=tax_year,
                members=members,
                org_type=org_type,
                source=self.name,
            ))

        return rosters

    def close(self):
        """Close the direct SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
