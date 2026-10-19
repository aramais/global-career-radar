"""Compare deterministic policy revisions on one read-only vacancy snapshot.

Git baselines include their Python code and profile/rule configuration. Each
revision runs in a separate process with an empty environment and no network or
SQLite access. Saved annotation facts are held fixed; paid routing, reranking,
annotation refresh, and the application pipeline are deliberately absent.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_intake.annotation.eval_runner import read_saved_records, write_report
from job_intake.config.settings import AppConfig
from job_intake.models.job import JobRecord

RULES_DIFF_VERSION = "deterministic-rules-diff-v1"
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@~^{}+-]{0,255}")
_OUTCOME_FIELDS = {
    "job_uid", "profile_id", "profile_version", "decision", "tier", "score",
    "deterministic_score", "bridge_role", "reasons", "blocker_signals", "risks",
    "matched_signals", "fit_reason",
}
_COMPARE_FIELDS = sorted(_OUTCOME_FIELDS - {"job_uid", "profile_id", "profile_version"})

# This runner does not import this module from either revision. In particular, a
# baseline predating rules-diff remains evaluable without mixing current policy
# modules into its import graph. Only the deterministic contract is shared.
_WORKER = r'''
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.clear()

def offline(event, arguments):
    if event.startswith("socket.") or event in {
        "sqlite3.connect", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
    }:
        raise PermissionError("Offline rules-diff forbids network, SQLite, and subprocesses")
    if event == "open" and isinstance(arguments[0], (str, bytes)):
        name = os.fsdecode(arguments[0])
        if Path(name).name == ".env":
            raise PermissionError("Offline rules-diff does not read .env")

sys.addaudithook(offline)
sys.path.insert(0, sys.argv[1])

try:
    from job_intake.annotation.service import apply_annotation_constraints
    from job_intake.annotation.text import TEXT_VERSION, role_description
    from job_intake.models.job import FilterDecision, JobRecord, JobStatus
    from job_intake.profiles import load_streams
    from job_intake.scoring.tiering import finalize_tier

    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    streams = load_streams(payload["rules"], payload["profiles"])
    try:
        from job_intake.classification import RoleClassifier
    except ModuleNotFoundError as exc:
        if exc.name != "job_intake.classification":
            raise
        classifier = None
        policy = "multi-profile-legacy"
    else:
        from job_intake.profiles import with_other_stream
        streams = with_other_stream(streams, payload["rules"])
        classifier = RoleClassifier(streams)
        policy = "single-role-v1"
    policies = [(stream, stream.engine, stream.scorer) for stream in streams]
    evaluations = []
    for saved in payload["records"]:
        values = dict(saved["record"])
        if values.get("posted_at"):
            values["posted_at"] = datetime.fromisoformat(values["posted_at"])
        values["status"] = JobStatus(values["status"])
        record = JobRecord(**values)
        role_text = role_description(record)
        selected, classification = (classifier.classify(record, use_ai=False)
                                    if classifier else (None, {}))
        active = [(stream, engine, scorer) for stream, engine, scorer in policies
                  if selected is None or stream.id == selected.id]
        for stream, engine, scorer in active:
            item = engine.apply(record)
            apply_annotation_constraints(record, item.evaluation, engine)
            item.profile_id = stream.id
            if classifier and stream.id == "other":
                item.evaluation.fit_score = item.evaluation.deterministic_score = 0.0
                item.evaluation.bridge_role = False
                item.evaluation.fit_reason = classification["reason"]
                if item.evaluation.decision != FilterDecision.REJECT:
                    item.evaluation.decision = FilterDecision.REVIEW
                item.evaluation.risks.append("role_classification:needs_review")
            else:
                score_options = {"record": record} if hasattr(
                    stream.scoring, "scoring_policy"
                ) else {}
                scorer.score(
                    record.source, record.company, record.title,
                    role_text, item.evaluation, **score_options,
                )
            finalize_tier(item, stream.scoring.threshold_a, stream.scoring.threshold_b)
            result = item.evaluation
            evaluations.append({
                "job_uid": saved["job_uid"], "profile_id": stream.id,
                "profile_version": stream.version, "decision": result.decision.value,
                "tier": result.tier.value, "score": result.fit_score,
                "deterministic_score": result.deterministic_score,
                "bridge_role": result.bridge_role, "reasons": result.reasons,
                "blocker_signals": result.blocker_signals, "risks": result.risks,
                "matched_signals": result.matched_signals, "fit_reason": result.fit_reason,
            })
    print(json.dumps({
        "text_version": TEXT_VERSION, "policy": policy,
        "profiles": {stream.id: stream.version for stream in streams},
        "evaluations": evaluations,
    }, ensure_ascii=False, allow_nan=False))
except Exception as exc:
    # Historical code may include source text in exceptions. Never echo it.
    print(type(exc).__name__, file=sys.stderr)
    sys.exit(1)
'''


def _fingerprint(value: object) -> str:
    content = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _git(repository: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments], capture_output=True,
            check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Cannot read local Git repository for rules-diff") from None
    if result.returncode:
        # Git diagnostics may include arbitrary ref text; expose no raw output.
        raise ValueError("Cannot resolve or read the local rules-diff Git baseline")
    return result.stdout


def _resolve_ref(repository: Path, ref: str) -> str:
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise ValueError("baseline must be a safe Git commit ref or a rules-diff snapshot file")
    sha = _git(repository, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    value = sha.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ValueError("Git baseline did not resolve to a commit")
    return value


def _source_files(root: Path) -> dict[str, bytes]:
    package = root / "src" / "job_intake"
    if not package.is_dir():
        raise ValueError("rules-diff requires the src/job_intake package in the repository")
    result = {}
    for path in sorted(package.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("rules-diff does not accept symlinked Python source files")
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _baseline_files(repository: Path, sha: str, config_paths: list[str]) -> dict[str, bytes]:
    archive = _git(repository, "archive", "--format=tar", sha, "src/job_intake", *config_paths)
    result = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        for member in handle.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe file path in Git baseline")
            wanted = member.name in config_paths or (
                member.name.startswith("src/job_intake/") and member.name.endswith(".py")
            )
            if not wanted:
                continue
            if not member.isfile():
                raise ValueError("rules-diff baseline inputs must be regular files")
            content = handle.extractfile(member)
            if content is None:
                raise ValueError("Cannot read Git baseline file")
            result[member.name] = content.read()
    if any(path not in result for path in config_paths):
        raise ValueError("The baseline does not contain the current rule/profile config paths")
    return result


def _mapping(content: bytes) -> dict:
    try:
        value = yaml.safe_load(content.decode("utf-8")) or {}
    except (UnicodeError, yaml.YAMLError):
        raise ValueError("Invalid rules-diff YAML configuration") from None
    if not isinstance(value, dict):
        raise ValueError("rules-diff rule/profile configuration must be a mapping")
    return value


def _run_revision(
    root: Path, files: dict[str, bytes], config_paths: list[str], records: list[dict],
) -> dict:
    for name, content in files.items():
        if name.startswith("src/job_intake/") and name.endswith(".py"):
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    payload = root / "source-snapshot.json"
    payload.write_text(json.dumps({
        "rules": _mapping(files[config_paths[0]]),
        "profiles": _mapping(files[config_paths[1]]), "records": records,
    }, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    try:
        process = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _WORKER, str(root / "src"), str(payload)],
            cwd=root, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Offline rules-diff revision could not finish within 60 seconds") from None
    if process.returncode:
        kind = process.stderr.strip()
        diagnostic = kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", kind) else "error"
        raise ValueError(f"Offline rules-diff revision failed ({diagnostic})")
    try:
        result = json.loads(process.stdout)
    except (ValueError, RecursionError):
        raise ValueError("Offline rules-diff revision returned invalid results") from None
    _validate_revision(result, expected_jobs={row["job_uid"] for row in records})
    result["code_fingerprint"] = _fingerprint({
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items() if name.endswith(".py")
    })
    result["config_fingerprints"] = {
        name: hashlib.sha256(files[name]).hexdigest() for name in config_paths
    }
    return result


def _validate_revision(value: object, *, expected_jobs: set[str] | None = None) -> None:
    if (
        not isinstance(value, dict) or not isinstance(value.get("evaluations"), list)
        or not isinstance(value.get("profiles"), dict) or not value.get("profiles")
        or not isinstance(value.get("text_version"), str)
    ):
        raise ValueError("Invalid rules-diff revision snapshot")
    if any(
        not isinstance(key, str) or not isinstance(version, str)
        for key, version in value["profiles"].items()
    ):
        raise ValueError("Invalid rules-diff profile versions")
    seen = set()
    for row in value["evaluations"]:
        if not isinstance(row, dict) or set(row) != _OUTCOME_FIELDS:
            raise ValueError("Invalid rules-diff evaluation snapshot")
        for name in ("job_uid", "profile_id", "profile_version", "fit_reason"):
            if not isinstance(row[name], str):
                raise ValueError("Invalid rules-diff evaluation text field")
        key = (row["job_uid"], row["profile_id"])
        if key in seen or not all(key) or row["profile_id"] not in value["profiles"]:
            raise ValueError("Duplicate or unknown rules-diff evaluation identity")
        seen.add(key)
        if row["profile_version"] != value["profiles"][row["profile_id"]]:
            raise ValueError("Inconsistent rules-diff profile version")
        if (
            not isinstance(row["decision"], str)
            or row["decision"] not in {"pass", "reject", "review"}
            or not isinstance(row["tier"], str) or row["tier"] not in {"A", "B", "C"}
        ):
            raise ValueError("Invalid rules-diff evaluation outcome")
        for name in ("score", "deterministic_score"):
            number = row[name]
            if type(number) not in {int, float} or not math.isfinite(number):
                raise ValueError("Invalid rules-diff evaluation score")
        if type(row["bridge_role"]) is not bool:
            raise ValueError("Invalid rules-diff bridge role")
        for name in ("reasons", "blocker_signals", "risks", "matched_signals"):
            if not isinstance(row[name], list) or any(
                not isinstance(item, str) for item in row[name]
            ):
                raise ValueError("Invalid rules-diff evaluation explanation")
    policy = value.get("policy", "multi-profile-legacy")
    if policy not in {"single-role-v1", "multi-profile-legacy"}:
        raise ValueError("Invalid rules-diff classification policy")
    if policy == "single-role-v1":
        by_job = Counter(uid for uid, _ in seen)
        if any(count != 1 for count in by_job.values()) or (
            expected_jobs is not None and set(by_job) != expected_jobs
        ):
            raise ValueError(
                "rules-diff snapshot must contain exactly one selected role per vacancy"
            )
    elif expected_jobs is not None and seen != {
        (uid, profile) for uid in expected_jobs for profile in value["profiles"]
    }:
        raise ValueError("rules-diff snapshot must contain every selected vacancy/profile outcome")


def _snapshot_baseline(path: Path, source_fingerprint: str, job_uids: set[str]) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise ValueError("Cannot read rules-diff baseline snapshot") from None
    if (
        not isinstance(report, dict) or report.get("schema") != RULES_DIFF_VERSION
        or not isinstance(report.get("source_snapshot"), dict)
        or report.get("source_snapshot", {}).get("fingerprint") != source_fingerprint
    ):
        raise ValueError("Baseline snapshot must use the same saved vacancy/annotation inputs")
    result = report.get("current")
    _validate_revision(result, expected_jobs=job_uids)
    return {**result, "kind": "snapshot", "snapshot_path": str(path.resolve())}


def run_rules_diff(
    config: AppConfig, *, baseline: str, repository: Path | None = None,
    limit: int | None = None, job_uids: list[str] | None = None,
    include_archived: bool = False,
) -> dict:
    """Return free drift diagnostics, including a reusable current-result snapshot.

    ``baseline`` accepts a local Git commit ref or a previous rules-diff JSON
    report. Snapshot baselines must match these exact source and annotation inputs.
    Active vacancies are selected by default, using ``read_saved_records(mode=ro)``.
    Gold comparisons can explicitly include archived cohort members.
    """
    repository = (repository or Path.cwd()).expanduser().resolve()
    root = Path(_git(repository, "rev-parse", "--show-toplevel").decode().strip())
    paths = [
        config.rules_path.expanduser().resolve(),
        config.search_profiles_path.expanduser().resolve(),
    ]
    try:
        config_paths = [path.relative_to(root).as_posix() for path in paths]
    except ValueError:
        raise ValueError("rules-diff requires configs inside the Git repository") from None
    database, selected, problems = read_saved_records(
        config.database_url, limit=limit, job_uids=job_uids,
        include_archived=include_archived,
    )
    if problems:
        raise ValueError("rules-diff requires valid saved source and annotation records")
    if not selected:
        raise ValueError("No vacancies selected for rules-diff")
    records = []
    for uid, record in selected:
        saved = {
            field.name: getattr(record, field.name) for field in fields(JobRecord) if field.init
        }
        saved["posted_at"] = record.posted_at.isoformat() if record.posted_at else None
        saved["status"] = record.status.value
        records.append({"job_uid": uid, "record": saved})
    # Selection ordering may change between runs while the source remains equal.
    records.sort(key=lambda item: item["job_uid"])
    source_fingerprint = _fingerprint(records)
    candidate = Path(baseline).expanduser()
    if candidate.is_file():
        if candidate.suffix.lower() != ".json":
            raise ValueError("rules-diff snapshot baseline must be a JSON report")
        previous = _snapshot_baseline(
            candidate, source_fingerprint, {row["job_uid"] for row in records},
        )
        baseline_files = None
    else:
        sha = _resolve_ref(root, baseline)
        baseline_files = _baseline_files(root, sha, config_paths)
        previous = {"kind": "git", "ref": baseline, "commit": sha}
    current_files = _source_files(root)
    for path, name in zip(paths, config_paths, strict=True):
        current_files[name] = path.read_bytes()
    current_commit = _resolve_ref(root, "HEAD")
    with tempfile.TemporaryDirectory(prefix="job-intake-rules-diff-") as directory:
        temporary = Path(directory)
        if baseline_files is not None:
            previous.update(_run_revision(
                temporary / "baseline", baseline_files, config_paths, records,
            ))
        current = _run_revision(temporary / "current", current_files, config_paths, records)
    current.update(kind="working_tree", ref="working-tree", commit=current_commit)
    before = {(row["job_uid"], row["profile_id"]): row for row in previous["evaluations"]}
    after = {(row["job_uid"], row["profile_id"]): row for row in current["evaluations"]}
    changes = []
    transitions: Counter[str] = Counter()
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        changed = (
            [name for name in _COMPARE_FIELDS if old[name] != new[name]]
            if old is not None and new is not None else ["profile_presence"]
        )
        if not changed:
            continue
        changes.append({"job_uid": key[0], "profile_id": key[1], "changed_fields": changed,
                        "before": old, "after": new})
        if old is not None and new is not None:
            transitions[f"{old['decision']}/{old['tier']} → {new['decision']}/{new['tier']}"] += 1
    comparable = [entry for entry in changes if entry["before"] and entry["after"]]
    flips = [entry for entry in comparable if set(entry["changed_fields"]) & {"decision", "tier"}]
    return {
        "schema": RULES_DIFF_VERSION, "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database), "database_access": "read_only", "api_calls": 0,
        "scope": {
            "policy": "RoleClassifier (offline) + selected RuleEngine + "
                      "saved annotation constraints + DeterministicScorer + finalize_tier",
            "saved_annotation_facts": "fixed", "paid_prefilter": "excluded",
            "semantic_reranking": "excluded", "annotation_refresh": "excluded",
        },
        "selection": {"limit": limit, "job_uids": job_uids or [],
                      "include_archived": include_archived},
        "source_snapshot": {"fingerprint": source_fingerprint, "jobs": len(records)},
        "baseline": previous, "current": current,
        "counts": {
            "jobs": len(records), "evaluations_before": len(before),
            "evaluations_after": len(after),
            "changed_evaluations": len(changes), "flipped_evaluations": len(flips),
            "affected_jobs": len({entry["job_uid"] for entry in changes}),
            "decision_flips": sum("decision" in entry["changed_fields"] for entry in comparable),
            "tier_flips": sum("tier" in entry["changed_fields"] for entry in comparable),
            "score_changes": sum("score" in entry["changed_fields"] for entry in comparable),
            "profiles_added": sorted(current["profiles"].keys() - previous["profiles"].keys()),
            "profiles_removed": sorted(previous["profiles"].keys() - current["profiles"].keys()),
            "classification_policy_changed": (
                previous.get("policy", "multi-profile-legacy") != current.get("policy")
            ),
        },
        "decision_tier_transitions": dict(sorted(transitions.items())), "changes": changes,
        "flips": flips,
    }


def render_rules_diff_markdown(report: dict) -> str:
    """Summarize changes while retaining every flipped decision and its reasons."""
    counts = report["counts"]
    previous = report["baseline"]
    baseline = previous.get("ref") or previous.get("snapshot_path", "snapshot")
    lines = [
        "# Deterministic rules diff", "",
        f"Baseline: `{baseline}`; current: working tree.",
        f"Policies: {previous.get('policy', 'multi-profile-legacy')} → "
        f"{report['current'].get('policy', 'multi-profile-legacy')}.",
        f"Vacancies: {counts['jobs']}; API calls: 0; SQLite: read-only.",
        "Saved annotation facts are fixed. Paid routing, semantic reranking, and "
        "annotation refresh are excluded.", "",
        "| Measure | Count |", "| --- | ---: |",
        f"| Changed profile evaluations | {counts['changed_evaluations']} |",
        f"| Decision/tier flips | {counts['flipped_evaluations']} |",
        f"| Decision flips | {counts['decision_flips']} |",
        f"| Tier flips | {counts['tier_flips']} |",
        f"| Score changes | {counts['score_changes']} |", "",
        f"Source snapshot: `{report['source_snapshot']['fingerprint']}`.",
        f"Text versions: `{previous['text_version']}` → `{report['current']['text_version']}`.",
        f"Code fingerprints: `{previous.get('code_fingerprint', 'unavailable')}` → "
        f"`{report['current']['code_fingerprint']}`.", "",
    ]
    for label, key in (
        ("Added profiles", "profiles_added"), ("Removed profiles", "profiles_removed"),
    ):
        if counts[key]:
            names = ", ".join(f"`{value}`" for value in counts[key])
            lines.extend([f"{label}: {names}.", ""])
    if not report["flips"]:
        lines.extend(["No deterministic decisions or tiers flipped.", ""])
    for entry in report["flips"]:
        old, new = entry["before"], entry["after"]
        lines.extend([
            f"## `{entry['job_uid']}` · `{entry['profile_id']}`", "",
            f"`{old['decision']}/{old['tier']}` (score {old['score']:g}) → "
            f"`{new['decision']}/{new['tier']}` (score {new['score']:g}).", "",
        ])
        for label, row in (("Before", old), ("After", new)):
            reasons = row["reasons"] + row["blocker_signals"] + row["risks"]
            explanation = "; ".join(reasons) or row["fit_reason"] or "No recorded reasons"
            # Source-derived reasons are plain text, never artifact instructions.
            explanation = explanation.replace("\n", " ").replace("<", "&lt;").replace(
                ">", "&gt;",
            )
            lines.append(f"{label}: {explanation}")
        lines.append("")
    return "\n".join(lines)


def write_rules_diff_report(
    report: dict, output: Path, *, protected_paths: list[Path],
) -> tuple[Path, Path]:
    """Atomically write both JSON and Markdown, protecting all inputs first."""
    output = output.expanduser().resolve()
    markdown = output.with_suffix(".md")
    protected = {path.expanduser().resolve() for path in protected_paths}
    if output == markdown or output in protected or markdown in protected:
        raise ValueError("Rules-diff reports must not overwrite the database or other inputs")
    summary = render_rules_diff_markdown(report)
    json_path = write_report(report, output, protected_paths=protected_paths + [markdown])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=markdown.parent, prefix=markdown.name + ".",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(summary)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(markdown)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return json_path, markdown
