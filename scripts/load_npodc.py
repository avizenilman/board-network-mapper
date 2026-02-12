"""Load NCCS/NPODC IRS 990 Part VII compensation data into SQLite.

Data source: NCCS Efile Data Catalog v2.1
  https://nccs.urban.org/nccs/catalogs/catalog-efile-v2_1.html
Table: F9-P07-T01-COMPENSATION (1:M individual-level records)

Downloads from:
  https://nccs-efile.s3.us-east-1.amazonaws.com/public/efile_v2_1/F9-P07-T01-COMPENSATION-{YEAR}.CSV

CSV columns we use:
  ORG_EIN, ORG_NAME_L1, TAX_YEAR, TAX_PERIOD_BEGIN_DATE, TAX_PERIOD_END_DATE,
  RETURN_TYPE,
  F9_07_COMP_DTK_NAME_PERS (person name),
  F9_07_COMP_DTK_TITLE (title/role),
  F9_07_COMP_DTK_POS_INDIV_TRUST_X (individual trustee/director),
  F9_07_COMP_DTK_POS_OFF_X (officer),
  F9_07_COMP_DTK_POS_KEY_EMPL_X (key employee),
  F9_07_COMP_DTK_POS_HIGH_COMP_X (highest compensated),
  F9_07_COMP_DTK_POS_FORMER_X (former),
  F9_07_COMP_DTK_COMP_ORG (compensation from org),
  F9_07_COMP_DTK_COMP_RLTD (compensation from related orgs),
  F9_07_COMP_DTK_COMP_OTH (other compensation),
  F9_07_COMP_DTK_AVE_HOUR_WEEK (average hours/week)
"""

import csv
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "board_mapper.db"

# Years to load (in order from most recent)
YEARS = [2024, 2023, 2022, 2021, 2020, 2019, 2018]

# CSV column mapping: csv_col -> sqlite_col
COLUMN_MAP = {
    "ORG_EIN": "ein",
    "ORG_NAME_L1": "org_name",
    "TAX_YEAR": "tax_year",
    "TAX_PERIOD_BEGIN_DATE": "tax_period_begin",
    "TAX_PERIOD_END_DATE": "tax_period_end",
    "RETURN_TYPE": "return_type",
    "F9_07_COMP_DTK_NAME_PERS": "person_name",
    "F9_07_COMP_DTK_TITLE": "title",
    "F9_07_COMP_DTK_AVE_HOUR_WEEK": "avg_hours",
    "F9_07_COMP_DTK_POS_INDIV_TRUST_X": "is_trustee_director",
    "F9_07_COMP_DTK_POS_INST_TRUST_X": "is_inst_trustee",
    "F9_07_COMP_DTK_POS_OFF_X": "is_officer",
    "F9_07_COMP_DTK_POS_KEY_EMPL_X": "is_key_employee",
    "F9_07_COMP_DTK_POS_HIGH_COMP_X": "is_highest_compensated",
    "F9_07_COMP_DTK_POS_FORMER_X": "is_former",
    "F9_07_COMP_DTK_COMP_ORG": "comp_org",
    "F9_07_COMP_DTK_COMP_RLTD": "comp_related",
    "F9_07_COMP_DTK_COMP_OTH": "comp_other",
}

# Columns that need the X-flag -> boolean conversion
FLAG_COLS = {
    "is_trustee_director", "is_inst_trustee", "is_officer",
    "is_key_employee", "is_highest_compensated", "is_former",
}

# Compensation columns that need float conversion
COMP_COLS = {"comp_org", "comp_related", "comp_other"}


def clean_ein(raw: str) -> str:
    """Normalize EIN: strip prefix, dashes, leading zeros issue."""
    if not raw:
        return ""
    # Strip 'EIN-' prefix if present (v2.1 format has it in EIN2 but not ORG_EIN)
    s = raw.strip().replace("-", "").replace("EIN", "")
    # Remove any non-digit chars
    s = re.sub(r'\D', '', s)
    # Pad to 9 digits
    return s.zfill(9) if s else ""


def clean_flag(raw: str) -> int:
    """Convert X/TRUE/1/etc to 1, everything else to 0."""
    if not raw:
        return 0
    s = raw.strip().upper()
    return 1 if s in ("X", "TRUE", "1", "T", "YES") else 0


def clean_comp(raw: str) -> float:
    """Parse compensation field to float."""
    if not raw:
        return 0.0
    s = raw.strip().replace(",", "").replace("$", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def clean_name(raw: str) -> str:
    """Basic name cleanup."""
    if not raw:
        return ""
    return raw.strip()


def split_name(full_name: str) -> tuple[str, str]:
    """Split 'LAST FIRST' or 'FIRST LAST' into (first, last).

    IRS 990 names are typically in LAST, FIRST format or just LAST FIRST.
    We detect the comma-separated pattern first.
    """
    if not full_name:
        return ("", "")

    name = full_name.strip()

    # "LAST, FIRST MIDDLE" pattern
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else ""
        # Take just first name (drop middle)
        first = first.split()[0] if first else ""
        return (first, last)

    # Space-separated: assume "FIRST LAST" (less common in 990 data)
    parts = name.split()
    if len(parts) == 1:
        return ("", parts[0])
    if len(parts) == 2:
        return (parts[0], parts[1])
    # 3+ parts: first word is first name, rest is last
    return (parts[0], " ".join(parts[1:]))


def create_table(conn: sqlite3.Connection):
    """Create the npodc_compensation table (drop if exists)."""
    conn.executescript("""
        DROP TABLE IF EXISTS npodc_compensation;

        CREATE TABLE npodc_compensation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ein TEXT NOT NULL,
            org_name TEXT NOT NULL DEFAULT '',
            tax_year INTEGER NOT NULL,
            tax_period_begin TEXT DEFAULT '',
            tax_period_end TEXT DEFAULT '',
            return_type TEXT DEFAULT '',
            person_name TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            title TEXT DEFAULT '',
            avg_hours TEXT DEFAULT '',
            is_trustee_director INTEGER DEFAULT 0,
            is_inst_trustee INTEGER DEFAULT 0,
            is_officer INTEGER DEFAULT 0,
            is_key_employee INTEGER DEFAULT 0,
            is_highest_compensated INTEGER DEFAULT 0,
            is_former INTEGER DEFAULT 0,
            comp_org REAL DEFAULT 0.0,
            comp_related REAL DEFAULT 0.0,
            comp_other REAL DEFAULT 0.0
        );
    """)
    conn.commit()


def create_indexes(conn: sqlite3.Connection):
    """Create indexes for fast querying."""
    print("Creating indexes...")
    t0 = time.time()
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_npodc_last_first
            ON npodc_compensation (last_name COLLATE NOCASE, first_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_npodc_ein
            ON npodc_compensation (ein);
        CREATE INDEX IF NOT EXISTS idx_npodc_ein_year
            ON npodc_compensation (ein, tax_year DESC);
        CREATE INDEX IF NOT EXISTS idx_npodc_name_person
            ON npodc_compensation (person_name COLLATE NOCASE);
    """)
    conn.commit()
    print(f"  Indexes created in {time.time() - t0:.1f}s")


def load_csv(conn: sqlite3.Connection, csv_path: str, year: int) -> int:
    """Load a single CSV file into the database. Returns row count."""
    if not os.path.exists(csv_path):
        print(f"  SKIP: {csv_path} not found")
        return 0

    file_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  Loading {os.path.basename(csv_path)} ({file_size_mb:.0f} MB)...")
    t0 = time.time()

    insert_sql = """
        INSERT INTO npodc_compensation (
            ein, org_name, tax_year, tax_period_begin, tax_period_end,
            return_type, person_name, first_name, last_name, title,
            avg_hours, is_trustee_director, is_inst_trustee, is_officer,
            is_key_employee, is_highest_compensated, is_former,
            comp_org, comp_related, comp_other
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    skipped = 0
    batch = []
    batch_size = 10000

    # Use large buffer for reading
    with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)

        for row in reader:
            ein = clean_ein(row.get("ORG_EIN", ""))
            person_name = clean_name(row.get("F9_07_COMP_DTK_NAME_PERS", ""))

            # Skip rows without EIN or person name
            if not ein or not person_name:
                skipped += 1
                continue

            first, last = split_name(person_name)

            record = (
                ein,
                clean_name(row.get("ORG_NAME_L1", "")),
                year,
                row.get("TAX_PERIOD_BEGIN_DATE", ""),
                row.get("TAX_PERIOD_END_DATE", ""),
                row.get("RETURN_TYPE", ""),
                person_name,
                first,
                last,
                clean_name(row.get("F9_07_COMP_DTK_TITLE", "")),
                row.get("F9_07_COMP_DTK_AVE_HOUR_WEEK", ""),
                clean_flag(row.get("F9_07_COMP_DTK_POS_INDIV_TRUST_X", "")),
                clean_flag(row.get("F9_07_COMP_DTK_POS_INST_TRUST_X", "")),
                clean_flag(row.get("F9_07_COMP_DTK_POS_OFF_X", "")),
                clean_flag(row.get("F9_07_COMP_DTK_POS_KEY_EMPL_X", "")),
                clean_flag(row.get("F9_07_COMP_DTK_POS_HIGH_COMP_X", "")),
                clean_flag(row.get("F9_07_COMP_DTK_POS_FORMER_X", "")),
                clean_comp(row.get("F9_07_COMP_DTK_COMP_ORG", "")),
                clean_comp(row.get("F9_07_COMP_DTK_COMP_RLTD", "")),
                clean_comp(row.get("F9_07_COMP_DTK_COMP_OTH", "")),
            )

            batch.append(record)
            count += 1

            if len(batch) >= batch_size:
                conn.executemany(insert_sql, batch)
                batch.clear()
                if count % 500000 == 0:
                    conn.commit()
                    print(f"    ...{count:,} rows ({time.time() - t0:.0f}s)")

        # Flush remaining
        if batch:
            conn.executemany(insert_sql, batch)
        conn.commit()

    elapsed = time.time() - t0
    print(f"    {count:,} rows loaded, {skipped:,} skipped ({elapsed:.1f}s)")
    return count


def print_stats(conn: sqlite3.Connection):
    """Print summary statistics about the loaded data."""
    print("\n=== NPODC Compensation Database Stats ===")

    # Total records
    total = conn.execute("SELECT COUNT(*) FROM npodc_compensation").fetchone()[0]
    print(f"Total records: {total:,}")

    # Year range
    years = conn.execute(
        "SELECT MIN(tax_year), MAX(tax_year) FROM npodc_compensation"
    ).fetchone()
    print(f"Year range: {years[0]} - {years[1]}")

    # Records per year
    print("\nRecords by year:")
    rows = conn.execute(
        "SELECT tax_year, COUNT(*) as cnt FROM npodc_compensation GROUP BY tax_year ORDER BY tax_year"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]:>10,}")

    # Unique organizations
    orgs = conn.execute("SELECT COUNT(DISTINCT ein) FROM npodc_compensation").fetchone()[0]
    print(f"\nUnique organizations (EINs): {orgs:,}")

    # Unique people (approximate by name)
    people = conn.execute(
        "SELECT COUNT(DISTINCT person_name) FROM npodc_compensation"
    ).fetchone()[0]
    print(f"Unique person names: {people:,}")

    # Transportation Alternatives check
    ta_ein = "510186015"
    ta_count = conn.execute(
        "SELECT COUNT(*) FROM npodc_compensation WHERE ein = ?", (ta_ein,)
    ).fetchone()[0]
    print(f"\nTransportation Alternatives (EIN {ta_ein}): {ta_count} records")

    if ta_count > 0:
        print("  TA records by year:")
        ta_rows = conn.execute(
            "SELECT tax_year, COUNT(*) FROM npodc_compensation WHERE ein = ? GROUP BY tax_year ORDER BY tax_year",
            (ta_ein,)
        ).fetchall()
        for r in ta_rows:
            print(f"    {r[0]}: {r[1]} people")

        print("  Most recent TA roster:")
        ta_latest = conn.execute(
            """SELECT person_name, title, comp_org, is_trustee_director, is_officer, is_key_employee
               FROM npodc_compensation
               WHERE ein = ?
               ORDER BY tax_year DESC, comp_org DESC
               LIMIT 20""",
            (ta_ein,)
        ).fetchall()
        for r in ta_latest:
            flags = []
            if r[3]: flags.append("Dir")
            if r[4]: flags.append("Off")
            if r[5]: flags.append("Key")
            flag_str = ",".join(flags) if flags else "-"
            print(f"    {r[0]:<30s} {r[1]:<30s} ${r[2]:>10,.0f}  [{flag_str}]")

    # Janet Liff check
    print("\nJanet Liff search:")
    liff_rows = conn.execute(
        """SELECT person_name, org_name, ein, tax_year, title, comp_org
           FROM npodc_compensation
           WHERE last_name LIKE 'LIFF%' AND first_name LIKE 'JANET%'
           ORDER BY tax_year DESC""",
    ).fetchall()
    if liff_rows:
        for r in liff_rows:
            print(f"  {r[0]} @ {r[1]} (EIN:{r[2]}, {r[3]}) - {r[4]}, ${r[5]:,.0f}")
    else:
        # Try broader search
        liff_rows2 = conn.execute(
            """SELECT person_name, org_name, ein, tax_year, title, comp_org
               FROM npodc_compensation
               WHERE person_name LIKE '%LIFF%JANET%' OR person_name LIKE '%JANET%LIFF%'
               ORDER BY tax_year DESC""",
        ).fetchall()
        if liff_rows2:
            for r in liff_rows2:
                print(f"  {r[0]} @ {r[1]} (EIN:{r[2]}, {r[3]}) - {r[4]}, ${r[5]:,.0f}")
        else:
            print("  Not found in loaded data")

    # DB file size
    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"\nDatabase file size: {db_size_mb:.1f} MB")


def main():
    print(f"NPODC 990 Compensation Data Loader")
    print(f"DB: {DB_PATH}")
    print(f"Data dir: {DATA_DIR}")
    print(f"Years: {YEARS}")
    print()

    # Find available CSV files
    available = []
    for year in YEARS:
        csv_path = DATA_DIR / f"F9-P07-T01-COMPENSATION-{year}.CSV"
        if csv_path.exists():
            available.append((year, str(csv_path)))
        else:
            print(f"  {year}: not found at {csv_path}")

    if not available:
        print("ERROR: No CSV files found in data/. Download them first:")
        print("  curl -o data/F9-P07-T01-COMPENSATION-2024.CSV \\")
        print("    https://nccs-efile.s3.us-east-1.amazonaws.com/public/efile_v2_1/F9-P07-T01-COMPENSATION-2024.CSV")
        sys.exit(1)

    print(f"\nFound {len(available)} CSV files to load:")
    for year, path in available:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  {year}: {os.path.basename(path)} ({size_mb:.0f} MB)")

    # Connect to DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-500000")  # 500MB cache
    conn.execute("PRAGMA temp_store=MEMORY")

    # Create table (drops existing)
    print("\nCreating table...")
    create_table(conn)

    # Load each year
    total_loaded = 0
    t_start = time.time()
    for year, csv_path in available:
        loaded = load_csv(conn, csv_path, year)
        total_loaded += loaded

    elapsed = time.time() - t_start
    print(f"\nTotal: {total_loaded:,} rows loaded in {elapsed:.1f}s")

    # Create indexes
    create_indexes(conn)

    # Print stats
    conn.row_factory = sqlite3.Row
    print_stats(conn)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
