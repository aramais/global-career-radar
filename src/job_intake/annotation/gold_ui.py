"""Manual, source-only web labeling over the canonical gold JSONL.

SQLite is opened only through the read-only evaluator. Changes are serialized
with a separate advisory lock, backed up, journaled and published atomically.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from job_intake.annotation.eval_runner import read_saved_records, sqlite_path
from job_intake.annotation.gold import (
    _METADATA_FIELDS,
    _SOURCE_FIELDS,
    _snapshot,
    _source_error,
    validate_gold,
)
from job_intake.annotation.gold_workbook import configured_roles
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig

_LABEL_FIELDS = ("role_label", "relevance", "gate_outcome", "notes")
_GATES = ("eligible", "ineligible", "unknown")
_LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")
_SOURCE_WARNINGS = {
    "source_hash_changed": "Исходный текст изменился. Проверьте расхождение перед разметкой.",
    "text_version_changed": "Изменилась версия обработки текста; проверьте исходник и цитаты.",
}


class GoldUIError(ValueError):
    """An expected labeling error that the web adapter can map to HTTP."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _complete(case: dict) -> bool:
    return all(case.get(field) is not None
               for field in ("role_label", "relevance", "gate_outcome"))


def _has_labels(case: dict) -> bool:
    return any(case.get(field) is not None
               for field in ("role_label", "relevance", "gate_outcome")) \
        or bool(case.get("tags")) or bool(case.get("notes"))


def _unique_object(pairs: list[tuple]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value: " + value)


def _safe_source(case: dict) -> dict:
    """Never return arbitrary metadata, annotation, scores or model outputs."""
    source = case.get("source")
    if not isinstance(source, dict):
        return {}
    result = {field: value for field in _SOURCE_FIELDS
              if (value := source.get(field)) is None or isinstance(value, str)}
    metadata = source.get("source_metadata")
    if isinstance(metadata, dict):
        result["source_metadata"] = {
            field: copy.deepcopy(metadata[field]) for field in _METADATA_FIELDS
            if field in metadata and (
                type(metadata[field]) is bool if field == "description_complete" else
                isinstance(metadata[field], str) or isinstance(metadata[field], list)
                and all(isinstance(item, str) for item in metadata[field])
            )
        }
    return result


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class GoldLabelStore:
    """A lazy JSONL store; constructing it never touches gold or the database."""

    def __init__(self, config: AppConfig, path: Path | None = None):
        self.config = config
        self._path = path.expanduser().resolve() if path is not None else None

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        try:
            return sqlite_path(self.config.database_url).parent \
                / "gold" / "vacancy-tags-v2-labeled.jsonl"
        except ValueError as error:
            raise GoldUIError("Для gold dataset нужен файловый SQLite или явный --gold: "
                              + str(error), 404) from error

    def _roles(self) -> list[dict]:
        try:
            return [{"value": role["id"], "label": role["name"]}
                    for role in configured_roles(self.config)]
        except (OSError, ValueError, KeyError, TypeError) as error:
            message = "Не удалось загрузить список профессий: " + str(error)
            raise GoldUIError(message, 500) from error

    def _read(self) -> tuple[bytes, list[bytes], list[tuple[int, dict]]]:
        try:
            content = self.path.read_bytes()
        except FileNotFoundError as error:
            raise GoldUIError(
                "Gold dataset не найден. Подготовьте JSONL или укажите существующий файл "
                "через --gold: " + str(self.path), 404,
            ) from error
        except OSError as error:
            raise GoldUIError("Не удалось прочитать gold dataset: " + str(error), 500) from error
        lines, cases, seen = content.splitlines(keepends=True), [], set()
        try:
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                case = json.loads(line, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
                if not isinstance(case, dict) or not isinstance(case.get("job_uid"), str) \
                        or not case["job_uid"].strip() or case["job_uid"] in seen:
                    raise ValueError("Ожидалась уникальная вакансия с job_uid")
                seen.add(case["job_uid"])
                cases.append((index, case))
            if not cases:
                raise ValueError("Gold dataset пуст")
        except (ValueError, UnicodeError, RecursionError) as error:
            raise GoldUIError("Некорректный gold dataset: " + str(error), 500) from error
        return content, lines, cases

    @staticmethod
    def _find(uid: str, cases: list[tuple[int, dict]]) -> tuple[int, dict]:
        for index, case in cases:
            if case["job_uid"] == uid:
                return index, case
        raise GoldUIError("Вакансия не найдена в gold dataset", 404)

    def list_cases(self, query: dict) -> dict:
        """Return source-only summaries; filters never read pipeline evaluations."""
        if not isinstance(query, dict) or set(query) - {"status", "q", "offset", "limit"}:
            raise GoldUIError("Неизвестные параметры списка")
        status = query.get("status", "all")
        if status not in ("all", "unlabeled", "partial", "complete"):
            raise GoldUIError("Неизвестный статус разметки")
        search = query.get("q", "")
        if not isinstance(search, str):
            raise GoldUIError("Поиск должен быть текстом")
        numbers = {}
        for name, default in (("offset", 0), ("limit", 100)):
            value = query.get(name, default)
            if isinstance(value, str) and value.isascii() and value.isdigit():
                value = int(value)
            if type(value) is not int or value < (1 if name == "limit" else 0):
                raise GoldUIError("Некорректный параметр " + name)
            numbers[name] = min(value, 200) if name == "limit" else value
        _, _, cases = self._read()
        rows = []
        for _, case in cases:
            source = _safe_source(case)
            rows.append({
                "job_uid": case["job_uid"], "company": source.get("company") or "",
                "title": source.get("title") or "",
                **{field: case.get(field) for field in _LABEL_FIELDS},
                "complete": _complete(case), "has_labels": _has_labels(case),
            })
        stats = {
            "total": len(rows), "labeled": sum(row["complete"] for row in rows),
            "partial": sum(row["has_labels"] and not row["complete"] for row in rows),
            "unlabeled": sum(not row["has_labels"] for row in rows),
            "relevant": sum(type(row["relevance"]) is int and row["relevance"] >= 2
                            for row in rows),
            "strong": sum(type(row["relevance"]) is int and row["relevance"] == 3
                          for row in rows),
        }
        needle = search.strip().casefold()
        selected = [row for row in rows if (
            status == "all" or status == "complete" and row["complete"]
            or status == "unlabeled" and not row["has_labels"]
            or status == "partial" and row["has_labels"] and not row["complete"]
        ) and (not needle or needle in " ".join(
            str(row.get(field) or "") for field in ("company", "title", "notes", "job_uid")
        ).casefold())]
        offset, limit = numbers["offset"], numbers["limit"]
        return {"cases": selected[offset:offset + limit], "stats": stats,
                "offset": offset, "limit": limit, "total": len(selected),
                "has_more": offset + limit < len(selected), "profiles": self._roles()}

    def _source_records(self, uid: str) -> tuple:
        """Resolve immutable source facts; discovery can supply its own artifact."""
        return read_saved_records(self.config.database_url, limit=None, job_uids=[uid])

    def _detail(self, case: dict, line: bytes) -> dict:
        uid, warnings, can_save, state = case["job_uid"], [], True, "current"
        try:
            _, rows, problems = self._source_records(uid)
        except (OSError, ValueError, sqlite3.Error) as error:
            warnings.append("Источник недоступен: " + str(error))
            can_save = False
            state = "missing" if "Requested jobs" in str(error) else "unavailable"
        else:
            record = dict(rows)[uid]
            report = validate_gold(rows, [case], profile_ids=[role["value"]
                                                           for role in self._roles()])
            warnings.extend(_SOURCE_WARNINGS.get(item["code"], item["message"])
                            for item in report["warnings"] if item["code"] != "unlabeled_case")
            if not report["valid"]:
                warnings.extend(item["message"] for item in report["errors"])
                can_save, state = False, "invalid"
            if any(item["severity"] == "error" and item["code"] != "invalid_annotation_json"
                   for item in problems.get(uid, [])):
                warnings.append("Исходные данные в базе повреждены; сохранение запрещено.")
                can_save, state = False, "invalid"
            if case.get("source_hash") != source_hash(record) \
                    or case.get("source") != _snapshot(record):
                warnings.append("Источник изменился или snapshot отсутствует. Разметка не будет "
                                "перенесена на другой текст; сначала проверьте расхождение.")
                can_save, state = False, "drift"
        if not isinstance(case.get("source"), dict) or _source_error(case.get("source")):
            warnings.append("Некорректный snapshot исходной вакансии.")
            can_save, state = False, "invalid"
        return {
            "job_uid": uid, "source": _safe_source(case),
            "labels": {field: copy.deepcopy(case.get(field)) for field in _LABEL_FIELDS},
            "version": _sha(line), "complete": _complete(case),
            "warnings": warnings, "can_save": can_save, "source_state": state,
        }

    def detail(self, uid: str) -> dict:
        _, lines, cases = self._read()
        index, case = self._find(uid, cases)
        return self._detail(case, lines[index])

    def _patch(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {*_LABEL_FIELDS, "expected_version"}:
            raise GoldUIError("Допустимы только решения, комментарий и expected_version")
        version = payload.get("expected_version")
        if not isinstance(version, str) or len(version) != 64 \
                or any(character not in "0123456789abcdef" for character in version):
            raise GoldUIError("Нужна версия вакансии expected_version")
        updates = {field: payload[field] for field in _LABEL_FIELDS if field in payload}
        roles = {role["value"] for role in self._roles()}
        if "role_label" in updates and updates["role_label"] is not None \
                and (not isinstance(updates["role_label"], str)
                     or updates["role_label"] not in roles):
            raise GoldUIError("Неизвестная профессия role_label")
        if "relevance" in updates and updates["relevance"] is not None \
                and (type(updates["relevance"]) is not int
                     or updates["relevance"] not in range(4)):
            raise GoldUIError("Релевантность должна быть целым числом от 0 до 3 или null")
        if "gate_outcome" in updates and updates["gate_outcome"] is not None \
                and (not isinstance(updates["gate_outcome"], str)
                     or updates["gate_outcome"] not in _GATES):
            raise GoldUIError("Неизвестное значение условий найма gate_outcome")
        if "notes" in updates and not isinstance(updates["notes"], str):
            raise GoldUIError("Комментарий должен быть текстом")
        return updates

    def _publish(self, before: bytes, after: bytes, case: dict, old_line: bytes,
                 new_line: bytes, changed_fields: list[str]) -> None:
        """Durable write-ahead audit: hashes distinguish prepared and applied edits."""
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex
        backup = backup_dir / (self.path.name + "." + stamp + ".bak")
        audit = backup.with_suffix(".audit.json")
        event = {
            "operation": "manual_gold_patch", "created_at": datetime.now(UTC).isoformat(),
            "job_uid": case["job_uid"], "method": case["method"],
            "source_hash": case["source_hash"], "changed_fields": changed_fields,
            "before_row_sha256": _sha(old_line), "after_row_sha256": _sha(new_line),
            "before_file_sha256": _sha(before), "after_file_sha256": _sha(after),
            "backup": backup.name,
            "commit_check": "after_file_sha256 identifies the published file; this journal "
                            "is written before publication and may survive an interrupted save",
        }
        for path, content in ((backup, before), (audit, (
            json.dumps(event, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"))):
            with path.open("xb") as handle:
                os.chmod(path, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        _fsync_directory(backup_dir)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.name + ".",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, stat.S_IMODE(self.path.stat().st_mode))
                handle.write(after)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, uid: str, payload: dict) -> dict:
        """Apply only explicit manual fields; another row's edit is merged, never lost."""
        updates = self._patch(payload)
        # Missing setup never silently creates a directory, lock or empty dataset.
        self._read()
        try:
            with self.path.with_name(self.path.name + ".lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                before, lines, cases = self._read()
                index, case = self._find(uid, cases)
                old_line = lines[index]
                if payload["expected_version"] != _sha(old_line):
                    raise GoldUIError("Разметка этой вакансии уже изменена. Обновите карточку "
                                      "перед сохранением.", 409)
                detail = self._detail(case, old_line)
                if not detail["can_save"]:
                    raise GoldUIError("Сохранение запрещено: " + "; ".join(detail["warnings"]), 409)
                changed_fields = [field for field, value in updates.items()
                                  if case.get(field) != value]
                if not changed_fields:
                    return {**detail, "changed": False}
                updated = copy.deepcopy(case)
                updated.update(updates)
                updated["labeled_at"] = datetime.now(_LOCAL_ZONE).date().isoformat()
                if updated.get("method") is None:
                    updated["method"] = "blind"
                # Validate the exact result, including preserved curated evidence.
                proposed = self._detail(updated, old_line)
                if not proposed["can_save"]:
                    raise GoldUIError("Некорректная разметка: " + "; ".join(proposed["warnings"]))
                ending = b"\r\n" if old_line.endswith(b"\r\n") else (
                    b"\n" if old_line.endswith(b"\n") else b""
                )
                new_line = (json.dumps(updated, ensure_ascii=False, allow_nan=False)
                            .encode("utf-8") + ending)
                lines[index] = new_line
                self._publish(before, b"".join(lines), updated, old_line, new_line, changed_fields)
                return {**proposed, "version": _sha(new_line), "changed": True}
        except OSError as error:
            raise GoldUIError("Не удалось сохранить gold dataset: " + str(error), 500) from error
