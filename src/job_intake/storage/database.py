from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from job_intake.storage.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, future=True)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        self._apply_lightweight_migrations()

    def _apply_lightweight_migrations(self) -> None:
        """Add columns that ``create_all`` cannot add to a pre-existing table.

        ``create_all`` only creates missing tables, never alters existing ones, so a
        DB created before the ``semantic_score`` column existed would lack it. This is
        a minimal, idempotent additive migration until Alembic is introduced.
        """
        inspector = inspect(self.engine)
        if "jobs" not in inspector.get_table_names():
            return
        columns = {col["name"] for col in inspector.get_columns("jobs")}
        if "semantic_score" not in columns:
            with self.engine.begin() as connection:
                connection.execute(text("ALTER TABLE jobs ADD COLUMN semantic_score FLOAT"))

    def session(self) -> Session:
        return self.session_factory()
