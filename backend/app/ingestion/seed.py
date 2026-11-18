"""Demo application users (IT administrators). Clearly demo identities — not from the dataset.

For every company: `<slug>-admin` (admin: can approve), `<slug>-operator` (can propose),
`<slug>-viewer` (read-only).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, User

ROLES = ("admin", "operator", "viewer")


def seed_users(session: Session) -> list[User]:
    created = []
    existing = {u.id for u in session.scalars(select(User))}
    for c in session.scalars(select(Company).order_by(Company.id)):
        slug = c.id.split("-")[0]
        for role in ROLES:
            uid = f"{slug}-{role}"
            if uid in existing:
                continue
            u = User(id=uid, company_id=c.id, username=uid, display_name=f"{c.name} {role.title()}", role=role)
            session.add(u)
            created.append(u)
    session.flush()
    return created
