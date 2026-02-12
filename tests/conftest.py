"""Shared fixtures for board-network-mapper tests."""

import json
import os

import pytest

from core.database import Database
from sources.propublica import ProPublicaSource
from sources.littlesis import LittleSisSource


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def ground_truth():
    """Load TransAlt ground truth data from JSON fixture."""
    path = os.path.join(FIXTURES_DIR, "ground_truth_transalt.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary Database instance for testing."""
    db_path = str(tmp_path / "test_board_mapper.db")
    db = Database(db_path=db_path)
    yield db
    db.close()


@pytest.fixture
def propublica_source(test_db):
    """ProPublicaSource backed by a test database."""
    return ProPublicaSource(db=test_db)


@pytest.fixture
def littlesis_source(test_db):
    """LittleSisSource backed by a test database."""
    return LittleSisSource(db=test_db)
