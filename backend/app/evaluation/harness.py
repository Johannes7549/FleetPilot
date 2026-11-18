"""Builds an isolated, fully-ingested database for tests and evaluations.

Default: in-memory SQLite (fast, no services needed). Pass a PostgreSQL URL to run against the
production engine (schema is dropped and recreated — use a dedicated test database).
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import make_engine
from app.ingestion.loader import ingest_file
from app.ingestion.seed import seed_users
from app.models import Base


def build_database(url: str | None = None, dataset: str | None = None) -> sessionmaker[Session]:
    url = url or "sqlite://"
    engine = make_engine(url)
    if not url.startswith("sqlite"):
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if not url.startswith("sqlite"):
        from app.db.rls import apply_rls

        with engine.begin() as conn:  # same RLS policies as migration 0002
            apply_rls(conn)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with maker() as s:
        ingest_file(s, dataset or get_settings().dataset_path)
        seed_users(s)
        s.commit()
    return maker
