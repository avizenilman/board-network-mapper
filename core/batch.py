"""Batch mode: search multiple people, merge, export CSV."""

import csv
import hashlib
import io
import json
import os
from datetime import datetime
from typing import Optional

import pandas as pd

from core.models import CoConnection, SearchResult, Relationship
from core.database import Database


# Default CRM export columns (Salesforce NPSP compatible)
DEFAULT_COLUMNS = [
    "first_name",
    "last_name",
    "connected_to",
    "via_shared_orgs",
    "relationship_type",
    "overlap_count",
    "most_recent_year",
    "compensation",
    "source",
    "confidence",
    "notes",
    "stable_id",
    "is_current",
    "in_pipeline",
]

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "export_columns.json")


def get_export_columns() -> list[str]:
    """Get configured export columns, falling back to defaults."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            config = json.load(f)
            return config.get("columns", DEFAULT_COLUMNS)
    return DEFAULT_COLUMNS


def save_export_columns(columns: list[str]):
    """Save custom column configuration."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump({"columns": columns}, f, indent=2)


def connections_to_dataframe(
    connections: list[CoConnection],
    relationships: list[Relationship] = None,
    db: Database = None,
) -> pd.DataFrame:
    """Convert connections + relationships to a CRM-ready DataFrame."""
    rows = []

    for conn in connections:
        first, last = CoConnection.split_name(conn.name)
        is_excluded = db.is_excluded(conn.name) if db else False

        row = {
            "first_name": first,
            "last_name": last,
            "connected_to": ", ".join(conn.connected_via),
            "via_shared_orgs": ", ".join(conn.shared_orgs),
            "relationship_type": conn.member_type,
            "overlap_count": conn.overlap_count,
            "most_recent_year": conn.most_recent_overlap,
            "compensation": conn.compensation,
            "source": ", ".join(conn.sources),
            "confidence": conn.confidence,
            "notes": "; ".join(conn.roles),
            "stable_id": conn.stable_id,
            "is_current": conn.is_current,
            "in_pipeline": is_excluded,
        }
        rows.append(row)

    # Add relationship-based connections (LittleSis, web search)
    if relationships:
        seen_names = {r["first_name"].lower() + " " + r["last_name"].lower() for r in rows}
        for rel in relationships:
            name_lower = rel.related_to.lower().strip()
            if name_lower in seen_names:
                continue
            seen_names.add(name_lower)

            first, last = CoConnection.split_name(rel.related_to)
            is_excluded = db.is_excluded(rel.related_to) if db else False

            row = {
                "first_name": first,
                "last_name": last,
                "connected_to": rel.person_name,
                "via_shared_orgs": rel.context,
                "relationship_type": rel.relationship_type,
                "overlap_count": 0,
                "most_recent_year": rel.year,
                "compensation": 0.0,
                "source": rel.source,
                "confidence": rel.confidence,
                "notes": rel.context,
                "stable_id": hashlib.sha256(
                    f"{rel.person_name}|{rel.related_to}|{rel.relationship_type}".lower().encode()
                ).hexdigest()[:16],
                "is_current": True,
                "in_pipeline": is_excluded,
            }
            rows.append(row)

    df = pd.DataFrame(rows)

    # Filter to configured columns
    columns = get_export_columns()
    available = [c for c in columns if c in df.columns]
    df = df[available]

    return df


def export_csv(
    connections: list[CoConnection],
    relationships: list[Relationship] = None,
    db: Database = None,
    query_description: str = "",
    save_snapshot: bool = True,
) -> str:
    """Export connections to CSV string. Optionally saves snapshot to DB."""
    df = connections_to_dataframe(connections, relationships, db)
    csv_str = df.to_csv(index=False)

    if save_snapshot and db:
        db.save_snapshot(csv_str, query_description=query_description, row_count=len(df))

    return csv_str


def export_csv_file(
    connections: list[CoConnection],
    filepath: str,
    relationships: list[Relationship] = None,
    db: Database = None,
    query_description: str = "",
):
    """Export connections to a CSV file."""
    csv_str = export_csv(connections, relationships, db, query_description)
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        f.write(csv_str)


def diff_snapshots(db: Database, snapshot_id_old: int, snapshot_id_new: int) -> dict:
    """Compare two snapshots and return what changed."""
    old_csv = db.get_snapshot_data(snapshot_id_old)
    new_csv = db.get_snapshot_data(snapshot_id_new)

    if not old_csv or not new_csv:
        return {"error": "Snapshot not found"}

    old_df = pd.read_csv(io.StringIO(old_csv))
    new_df = pd.read_csv(io.StringIO(new_csv))

    # Use stable_id for comparison
    if "stable_id" not in old_df.columns or "stable_id" not in new_df.columns:
        return {"error": "Missing stable_id column"}

    old_ids = set(old_df["stable_id"].dropna())
    new_ids = set(new_df["stable_id"].dropna())

    added_ids = new_ids - old_ids
    removed_ids = old_ids - new_ids
    kept_ids = old_ids & new_ids

    added = new_df[new_df["stable_id"].isin(added_ids)]
    removed = old_df[old_df["stable_id"].isin(removed_ids)]

    return {
        "added": added.to_dict("records"),
        "removed": removed.to_dict("records"),
        "added_count": len(added),
        "removed_count": len(removed),
        "unchanged_count": len(kept_ids),
    }


def load_exclusion_list(db: Database, csv_path: str):
    """Load a CSV of known prospects into the exclusion list."""
    df = pd.read_csv(csv_path)

    names = []
    for _, row in df.iterrows():
        entry = {}
        # Try common column names
        for col in ["first_name", "First Name", "FirstName", "first"]:
            if col in df.columns:
                entry["first_name"] = str(row[col]).strip()
                break
        for col in ["last_name", "Last Name", "LastName", "last"]:
            if col in df.columns:
                entry["last_name"] = str(row[col]).strip()
                break
        for col in ["full_name", "Full Name", "Name", "name"]:
            if col in df.columns:
                entry["full_name"] = str(row[col]).strip()
                break

        if entry.get("first_name") or entry.get("full_name"):
            names.append(entry)

    db.clear_exclusions()
    db.load_exclusions(names)
    return len(names)
