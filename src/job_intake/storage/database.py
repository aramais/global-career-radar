from __future__ import annotations

from importlib import import_module
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from job_intake.storage.models import Base


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        if (
            parsed.drivername.startswith("sqlite")
            and parsed.database not in (None, ":memory:")
            and not parsed.database.startswith("file:")
        ):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, future=True)
        if parsed.drivername.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_foreign_keys)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, future=True)

    def create_schema(self) -> None:
        import_module("job_intake.crm.models")
        Base.metadata.create_all(self.engine)
        self._apply_lightweight_migrations()

    @staticmethod
    def _enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    # Additive columns that ``create_all`` cannot add to a pre-existing ``jobs`` table.
    _JOBS_ADDITIVE_COLUMNS = {
        "semantic_score": "FLOAT",
        "last_alerted_at": "DATETIME",
        "best_profile_id": "VARCHAR(100)",
        "best_profile_name": "VARCHAR(255)",
        "alert_pending": "BOOLEAN NOT NULL DEFAULT 0",
    }

    def _apply_lightweight_migrations(self) -> None:
        """Add columns that ``create_all`` cannot add to a pre-existing table.

        ``create_all`` only creates missing tables, never alters existing ones, so a
        DB created before a column existed would lack it. This is a minimal, idempotent
        additive migration until Alembic is introduced.
        """
        inspector = inspect(self.engine)
        if "jobs" not in inspector.get_table_names():
            return
        columns = {col["name"] for col in inspector.get_columns("jobs")}
        for name, ddl_type in self._JOBS_ADDITIVE_COLUMNS.items():
            if name not in columns:
                with self.engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl_type}"))

    def vacuum(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("VACUUM"))

    def session(self) -> Session:
        return self.session_factory()
