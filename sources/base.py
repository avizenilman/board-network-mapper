"""Base class for data sources."""

from abc import ABC, abstractmethod
from core.models import BoardSeat, BoardMember, Relationship, OrgRoster


class DataSource(ABC):
    """Interface for pluggable data sources."""
    name: str = "base"

    @abstractmethod
    async def search_person(self, name: str) -> list[BoardSeat]:
        """Given a name, return board seats found."""
        ...

    @abstractmethod
    async def search_org(self, ein: str) -> OrgRoster:
        """Given an EIN, return all officers/directors/employees."""
        ...

    async def search_relationships(self, name: str) -> list[Relationship]:
        """Given a name, return non-board relationships. Optional."""
        return []

    async def deep_research(self, name: str, context: dict = None) -> list[Relationship]:
        """Run structured web queries for deeper connections. Optional."""
        return []
