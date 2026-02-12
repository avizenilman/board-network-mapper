"""Pydantic models for board network data."""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import hashlib


class BoardMember(BaseModel):
    """A person listed on a 990 filing or org website."""
    name: str
    role: str  # "Director", "Chair", "Treasurer", "Exec Director"
    member_type: str  # "board", "officer", "staff", "advisory"
    is_board: bool = False  # 990 Part VII: Individual trustee or director
    is_officer: bool = False  # 990 Part VII: Officer
    is_key_employee: bool = False  # 990 Part VII: Key employee
    is_highest_compensated: bool = False
    is_former: bool = False
    compensation: float = 0.0
    compensation_related: float = 0.0
    compensation_other: float = 0.0
    source: str = ""

    @property
    def total_compensation(self) -> float:
        return self.compensation + self.compensation_related + self.compensation_other


class BoardSeat(BaseModel):
    """A person's position at an org, from a specific filing."""
    person_name: str
    org_name: str
    ein: str
    role: str
    member_type: str  # "board", "officer", "staff", "advisory"
    tax_period: str  # "2024-03"
    fiscal_year: int = 0
    compensation: float = 0.0
    compensation_related: float = 0.0
    compensation_other: float = 0.0
    source: str = ""
    is_current: bool = True
    org_type: str = "public_charity"  # or "private_foundation"
    city: str = ""
    state: str = ""

    @property
    def total_compensation(self) -> float:
        return self.compensation + self.compensation_related + self.compensation_other

    @property
    def stable_id(self) -> str:
        raw = f"{self.person_name.lower().strip()}|{self.ein}|{self.tax_period}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Relationship(BaseModel):
    """A non-board relationship between entities."""
    person_name: str
    related_to: str  # person or org name
    relationship_type: str  # "board_member", "spouse", "employee", "donor", "advisor"
    context: str = ""  # "both at D.E. Shaw"
    source: str = ""
    source_url: str = ""
    confidence: str = "high"  # "high", "medium", "low"
    year: str = ""


class CoConnection(BaseModel):
    """A person connected to the search target through shared orgs."""
    name: str
    first_name: str = ""
    last_name: str = ""
    shared_orgs: list[str] = Field(default_factory=list)
    shared_org_eins: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)
    member_type: str = "board"  # "board", "staff", "advisory", "spouse"
    roles: list[str] = Field(default_factory=list)
    most_recent_overlap: str = ""
    overlap_count: int = 0
    is_current: bool = True
    compensation: float = 0.0
    sources: list[str] = Field(default_factory=list)
    confidence: str = "high"
    spouse: str = ""
    connected_via: list[str] = Field(default_factory=list)  # which search targets connect here

    @property
    def stable_id(self) -> str:
        raw = f"{self.name.lower().strip()}|{'|'.join(sorted(self.shared_org_eins))}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @classmethod
    def split_name(cls, full_name: str) -> tuple[str, str]:
        parts = full_name.strip().split()
        if len(parts) == 0:
            return ("", "")
        if len(parts) == 1:
            return (parts[0], "")
        return (parts[0], " ".join(parts[1:]))


class SearchResult(BaseModel):
    """Complete results for a person lookup."""
    query_name: str
    timestamp: datetime = Field(default_factory=datetime.now)
    board_seats: list[BoardSeat] = Field(default_factory=list)
    connections: list[CoConnection] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    sources_failed: list[str] = Field(default_factory=list)
    mode: str = "quick"  # "quick" or "deep"


class OrgRoster(BaseModel):
    """All people listed on an org's filing."""
    org_name: str
    ein: str
    tax_period: str
    fiscal_year: int = 0
    members: list[BoardMember] = Field(default_factory=list)
    org_type: str = "public_charity"
    city: str = ""
    state: str = ""
    source: str = ""


class WebsiteMember(BaseModel):
    """A person scraped from an org's website."""
    name: str
    role: str = ""
    category: str = "board"  # "board", "advisory", "staff"
    title: str = ""
    bio: str = ""
    source_url: str = ""
