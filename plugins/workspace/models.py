"""Workspace data models — organization, member, and role primitives.

These are pure-data dataclasses with no storage or side effects.
The role hierarchy is immutable: Owner > Admin > Manager > Member > Guest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional


class Role(IntEnum):
    """Permission tier — higher numeric value means more privilege.

    The hierarchy is:
        Owner    = 100   — full control, can delete the org
        Admin    = 80    — manage members, settings
        Manager  = 60    — manage team workflows, approve actions
        Member   = 40    — use tools, create skills, participate
        Guest    = 20    — read-only, can query but not mutate
    """

    OWNER = 100
    ADMIN = 80
    MANAGER = 60
    MEMBER = 40
    GUEST = 20

    @classmethod
    def from_string(cls, s: str) -> "Role":
        """Case-insensitive lookup."""
        for role in cls:
            if role.name.lower() == s.lower():
                return role
        raise ValueError(f"Unknown role: {s}")

    def can_manage_members(self) -> bool:
        return self >= Role.ADMIN

    def can_write(self) -> bool:
        """Can create/update org resources."""
        return self >= Role.MEMBER

    def can_approve(self) -> bool:
        """Can approve gated actions."""
        return self >= Role.MANAGER

    def can_admin(self) -> bool:
        return self >= Role.ADMIN

    def is_owner(self) -> bool:
        return self == Role.OWNER


@dataclass(frozen=False)
class Organization:
    """A multi-user workspace owned by one organization."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    slug: str = ""
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    settings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "settings": self.settings,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Organization":
        import json

        settings = {}
        raw = row.get("settings")
        if raw:
            try:
                settings = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                pass
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row.get("description", ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            settings=settings,
        )


@dataclass(frozen=False)
class Member:
    """A user's membership in an organization with a specific role."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    organization_id: str = ""
    user_id: str = ""
    display_name: str = ""
    role: Role = Role.MEMBER
    joined_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "role": self.role.name.lower(),
            "joined_at": self.joined_at,
            "is_active": self.is_active,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Member":
        return cls(
            id=row["id"],
            organization_id=row["organization_id"],
            user_id=row["user_id"],
            display_name=row.get("display_name", ""),
            role=Role.from_string(row["role"]),
            joined_at=row["joined_at"],
            is_active=bool(row.get("is_active", True)),
        )
