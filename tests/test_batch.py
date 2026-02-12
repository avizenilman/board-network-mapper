"""Unit tests for batch mode helpers: snapshot diffing, exclusion loading, column config.

All tests run offline -- no network required.
"""

import csv
import io
import os
import tempfile

import pytest

from core.models import CoConnection, Relationship
from core.batch import (
    connections_to_dataframe,
    export_csv,
    export_csv_file,
    diff_snapshots,
    load_exclusion_list,
    get_export_columns,
    save_export_columns,
    DEFAULT_COLUMNS,
)


def _make_connections():
    return [
        CoConnection(
            name="Person A",
            first_name="Person",
            last_name="A",
            shared_orgs=["Org 1"],
            shared_org_eins=["111111111"],
            overlap_count=1,
            most_recent_overlap="2024-03",
            connected_via=["Target 1"],
            sources=["propublica_990"],
        ),
        CoConnection(
            name="Person B",
            first_name="Person",
            last_name="B",
            shared_orgs=["Org 1", "Org 2"],
            shared_org_eins=["111111111", "222222222"],
            overlap_count=2,
            most_recent_overlap="2024-06",
            connected_via=["Target 1", "Target 2"],
            sources=["propublica_990", "littlesis"],
            compensation=150000.0,
        ),
    ]


class TestSnapshotDiff:
    """Test diff_snapshots for comparing exports over time."""

    def test_diff_detects_added(self, test_db):
        """New rows in snapshot 2 are detected as added."""
        csv1 = "first_name,last_name,stable_id\nJanet,Liff,aaa111\n"
        csv2 = "first_name,last_name,stable_id\nJanet,Liff,aaa111\nMartin,Mignot,bbb222\n"

        test_db.save_snapshot(csv1, "snapshot 1", 1)
        test_db.save_snapshot(csv2, "snapshot 2", 2)

        snapshots = test_db.get_snapshots()
        # Most recent first
        diff = diff_snapshots(test_db, snapshots[1]["id"], snapshots[0]["id"])

        assert diff["added_count"] == 1
        assert diff["removed_count"] == 0
        assert diff["unchanged_count"] == 1

    def test_diff_detects_removed(self, test_db):
        """Rows in snapshot 1 but not 2 are detected as removed."""
        csv1 = "first_name,last_name,stable_id\nJanet,Liff,aaa111\nMartin,Mignot,bbb222\n"
        csv2 = "first_name,last_name,stable_id\nJanet,Liff,aaa111\n"

        test_db.save_snapshot(csv1, "snapshot 1", 2)
        test_db.save_snapshot(csv2, "snapshot 2", 1)

        snapshots = test_db.get_snapshots()
        diff = diff_snapshots(test_db, snapshots[1]["id"], snapshots[0]["id"])

        assert diff["removed_count"] == 1
        assert diff["added_count"] == 0

    def test_diff_missing_snapshot(self, test_db):
        """Missing snapshot returns error dict."""
        diff = diff_snapshots(test_db, 999, 998)
        assert "error" in diff


class TestExclusionLoading:
    """Test loading exclusion lists from CSV files."""

    def test_load_full_name_column(self, test_db, tmp_path):
        """Load exclusion CSV with 'Full Name' column."""
        csv_path = str(tmp_path / "exclusions.csv")
        with open(csv_path, "w") as f:
            f.write("Full Name\nJanet Liff\nBen Furnas\n")

        count = load_exclusion_list(test_db, csv_path)
        assert count == 2
        assert test_db.is_excluded("Janet Liff")
        assert test_db.is_excluded("Ben Furnas")

    def test_load_first_last_columns(self, test_db, tmp_path):
        """Load exclusion CSV with first_name/last_name columns."""
        csv_path = str(tmp_path / "exclusions.csv")
        with open(csv_path, "w") as f:
            f.write("first_name,last_name\nJanet,Liff\nBen,Furnas\n")

        count = load_exclusion_list(test_db, csv_path)
        assert count == 2
        assert test_db.is_excluded("Janet Liff")

    def test_load_clears_previous(self, test_db, tmp_path):
        """Loading a new exclusion list clears the old one."""
        test_db.load_exclusions([{"full_name": "Old Person"}])
        assert test_db.is_excluded("Old Person")

        csv_path = str(tmp_path / "exclusions.csv")
        with open(csv_path, "w") as f:
            f.write("Full Name\nNew Person\n")

        load_exclusion_list(test_db, csv_path)
        assert not test_db.is_excluded("Old Person")
        assert test_db.is_excluded("New Person")


class TestColumnConfig:
    """Test configurable column ordering."""

    def test_default_columns(self):
        """Default columns match the spec."""
        cols = get_export_columns()
        assert "first_name" in cols
        assert "last_name" in cols
        assert "stable_id" in cols
        assert "confidence" in cols

    def test_custom_columns(self, tmp_path):
        """Custom column config is respected."""
        config_path = str(tmp_path / "export_columns.json")
        import json
        with open(config_path, "w") as f:
            json.dump({"columns": ["last_name", "first_name", "source"]}, f)

        # Monkey-patch the config path for this test
        import core.batch as batch_module
        original_path = batch_module.CONFIG_PATH
        batch_module.CONFIG_PATH = config_path
        try:
            cols = get_export_columns()
            assert cols == ["last_name", "first_name", "source"]
        finally:
            batch_module.CONFIG_PATH = original_path


class TestCSVExportFile:
    """Test file-based CSV export."""

    def test_export_creates_file(self, tmp_path):
        """export_csv_file creates a file on disk."""
        connections = _make_connections()
        filepath = str(tmp_path / "output" / "test.csv")
        export_csv_file(connections, filepath)
        assert os.path.exists(filepath)

        with open(filepath) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2

    def test_export_dataframe_columns(self):
        """DataFrame has expected columns in correct order."""
        connections = _make_connections()
        df = connections_to_dataframe(connections)

        assert "first_name" in df.columns
        assert "last_name" in df.columns
        assert "stable_id" in df.columns

    def test_export_with_relationships(self):
        """Relationships are appended to the DataFrame."""
        connections = _make_connections()
        rels = [
            Relationship(
                person_name="Target 1",
                related_to="Spouse Person",
                relationship_type="spouse",
                source="littlesis",
                confidence="high",
            ),
        ]
        df = connections_to_dataframe(connections, relationships=rels)
        # Should have connections + relationship row
        assert len(df) == 3

    def test_relationships_deduped_against_connections(self):
        """Relationship names already in connections are not duplicated."""
        connections = [
            CoConnection(
                name="Person A",
                first_name="Person",
                last_name="A",
                shared_orgs=["Org 1"],
                shared_org_eins=["111"],
                overlap_count=1,
                most_recent_overlap="2024",
                sources=["propublica_990"],
            ),
        ]
        rels = [
            Relationship(
                person_name="Target",
                related_to="Person A",  # same as connection
                relationship_type="board_member",
                source="littlesis",
            ),
        ]
        df = connections_to_dataframe(connections, relationships=rels)
        # Person A should only appear once
        assert len(df) == 1
