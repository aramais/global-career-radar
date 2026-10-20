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
        # SQLite's built-in lower() only folds ASCII. Server-side vacancy search
        # must keep the previous browser search's Unicode case-insensitivity.
        connection.create_function(
            "hr_unicode_lower", 1,
            lambda value: value.lower() if isinstance(value, str) else value,
            deterministic=True,
        )

    # Additive columns that ``create_all`` cannot add to a pre-existing ``jobs`` table.
    _JOBS_ADDITIVE_COLUMNS = {
        "semantic_score": "FLOAT",
        "last_alerted_at": "DATETIME",
        "best_profile_id": "VARCHAR(100)",
        "best_profile_name": "VARCHAR(255)",
        "alert_pending": "BOOLEAN NOT NULL DEFAULT 0",
        "annotation": "JSON NOT NULL DEFAULT '{}'",
        "role_label": "VARCHAR(100)",
        "role_classification": "JSON NOT NULL DEFAULT '{}'",
        "archived_at": "DATETIME",
        "archive_version": "INTEGER NOT NULL DEFAULT 1",
        "source_revision": "INTEGER NOT NULL DEFAULT 1",
        "raw_fit_score": "FLOAT",
        "score_cap_reasons": "JSON NOT NULL DEFAULT '[]'",
        "eligibility_status": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "eligibility_evidence_status": "VARCHAR(32) NOT NULL DEFAULT 'unconfirmed'",
        "relevance": "INTEGER",
        "labels_status": "VARCHAR(32)",
        "labels_version": "VARCHAR(64)",
        "version_components": "JSON NOT NULL DEFAULT '{}'",
        "missing_since": "DATETIME",
        "missing_successes": "INTEGER NOT NULL DEFAULT 0",
        "missing_verified_seconds": "FLOAT NOT NULL DEFAULT 0",
        "missing_last_checked_at": "DATETIME",
        "last_complete_seen_at": "DATETIME",
    }

    def _apply_lightweight_migrations(self) -> None:
        """Add columns that ``create_all`` cannot add to a pre-existing table.

        ``create_all`` only creates missing tables, never alters existing ones, so a
        DB created before a column existed would lack it. This is a minimal, idempotent
        additive migration until Alembic is introduced.
        """
        inspector = inspect(self.engine)
        migrations = {
            "jobs": self._JOBS_ADDITIVE_COLUMNS,
            "crm_companies": {"version": "INTEGER NOT NULL DEFAULT 1"},
            "job_purpose_assessments": {
                "human_p1_source_hash": "VARCHAR(64)", "human_p2_source_hash": "VARCHAR(64)",
                "human_p1_rubric_version": "VARCHAR(100)",
                "human_p2_rubric_version": "VARCHAR(100)",
            },
            "job_relevance_evaluations": {
                "classification_version": "VARCHAR(64) NOT NULL DEFAULT ''",
                "classification_source_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
                "raw_fit_score": "FLOAT",
                "score_cap_reasons": "JSON NOT NULL DEFAULT '[]'",
                "eligibility_status": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
                "eligibility_evidence_status": "VARCHAR(32) NOT NULL DEFAULT 'unconfirmed'",
                "relevance": "INTEGER",
                "labels_status": "VARCHAR(32)",
                "labels_version": "VARCHAR(64)",
                "version_components": "JSON NOT NULL DEFAULT '{}'",
            },
        }
        tables = set(inspector.get_table_names())
        for table, additions in migrations.items():
            if table not in tables:
                continue
            columns = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl_type in additions.items():
                if name not in columns:
                    with self.engine.begin() as connection:
                        connection.execute(text(
                            f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"
                        ))

    def vacuum(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("VACUUM"))

    def session(self) -> Session:
        return self.session_factory()
