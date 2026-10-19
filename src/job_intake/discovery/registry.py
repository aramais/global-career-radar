"""Durable discovery memory, independent of the operational HR database.

Pending calls survive crashes. Their conservative TTL prevents a second process
from paying for the same unresolved request. Cached usage is provenance, never a
new expense. Secrets and transport objects do not belong in keys or payloads.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def contract_key(stage: str, value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False)
    return stage + ":v1:" + hashlib.sha256(encoded.encode()).hexdigest()


class DiscoveryRegistry:
    def __init__(self, path: Path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stage TEXT "
                       "NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created REAL "
                       "NOT NULL, expires REAL NOT NULL, origin TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM cache WHERE key=? AND expires>?",
                             (key, self.clock())).fetchone()
        if row is None:
            return None
        return {**dict(row), "payload": json.loads(row["payload"])}

    def claim(self, key: str, *, stage: str, origin: str, ttl: float = 3600,
              repeat: bool = False, reservation: dict | None = None) -> bool:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,expires FROM cache WHERE key=?", (key,)).fetchone()
            # Explicit refresh may replace a success, never another in-flight or
            # uncertain paid call. Uncertainty must first expire or be reconciled.
            if row and row["expires"] > self.clock() and (
                not repeat or row["status"] != "success"
            ):
                return False
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?)",
                       (key, stage, "pending", json.dumps({"reservation": reservation or {}},
                        allow_nan=False), self.clock(), self.clock() + ttl, origin))
        return True

    def put(self, key: str, *, stage: str, status: str, payload, origin: str,
            ttl: float) -> None:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?)",
                       (key, stage, status, encoded, self.clock(), self.clock() + ttl, origin))


def selection_contract(selector, *, prompt: str, units, snapshot: dict, plan: dict,
                       objective: str) -> dict:
    settings = {field: getattr(selector, field, None) for field in (
        "model", "provider", "endpoint", "effort", "thinking_level", "temperature",
        "max_output_tokens", "generation_config", "schema_version", "prompt_version",
    )}
    try:
        implementation = hashlib.sha256(inspect.getsource(type(selector)).encode()).hexdigest()
    except (OSError, TypeError):
        implementation = type(selector).__module__ + "." + type(selector).__qualname__
    return {"snapshot": snapshot, "prompt": prompt, "units": units,
            "examples": [*plan["seeds"], *plan.get("counterexamples", [])],
            "criteria": {k: v for k, v in plan.items() if k.startswith("criteria_")},
            "objective": objective, "settings": settings,
            "implementation": implementation}
