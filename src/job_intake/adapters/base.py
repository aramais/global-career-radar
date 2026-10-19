from __future__ import annotations

from abc import ABC, abstractmethod
from functools import wraps
from typing import Any

from job_intake.models.job import JobRecord


class SourceAuthenticationError(ValueError):
    """The configured source requires a valid authenticated session."""


def source_snapshot(scope: str):
    """Record a bounded source observation without treating search as a full board."""
    def decorate(method):
        @wraps(method)
        def collect(self, *args, **kwargs):
            self.board_snapshots.clear()
            self._snapshot_filtered = False
            self._snapshot_auth_required = False
            board_id = next((str(self.params[key]) for key in (
                "board_token", "board_name", "site", "url", "watchlist_path",
            ) if self.params.get(key)), self.name)
            snapshot = {
                "source": self.name, "board_id": board_id, "scope": scope,
                "adapter_type": type(self).__name__,
                "status": "failed", "snapshot_complete": False,
                "seen_job_ids": [], "seen_urls": [], "errors": [],
            }
            if type(self).__name__ == "LeverAdapter":
                snapshot["api_host"] = (
                    "api.eu.lever.co" if self.params.get("region") == "eu" else "api.lever.co"
                )
            try:
                jobs = method(self, *args, **kwargs)
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                snapshot["status"] = (
                    "auth_required" if isinstance(exc, SourceAuthenticationError)
                    or status_code in {401, 403} else "failed"
                )
                snapshot["errors"] = [type(exc).__name__]
                self.board_snapshots.append(snapshot)
                raise
            snapshot.update(
                status=("auth_required" if self._snapshot_auth_required else
                        "partial" if self.errors or self._snapshot_filtered else "success"),
                snapshot_complete=(scope == "board" and not self.errors
                                   and not self._snapshot_filtered
                                   and not self._snapshot_auth_required),
                seen_job_ids=sorted({str(job.source_job_id) for job in jobs if job.source_job_id}),
                seen_urls=sorted({job.original_url for job in jobs}),
                errors=list(self.errors),
                coverage={
                    "records": len(jobs), "complete_descriptions": sum(
                        job.source_metadata.get("description_complete") is True for job in jobs
                    ),
                },
                limits={key: self.params[key] for key in (
                    "max_pages", "page_size", "max_detail_fetches",
                ) if key in self.params},
                warnings=sorted({
                    str(job.source_metadata["detail_outcome"]) for job in jobs
                    if job.source_metadata.get("detail_outcome") == "quota_deferred"
                }),
            )
            self.board_snapshots.append(snapshot)
            return jobs
        return collect
    return decorate


class JobSourceAdapter(ABC):
    def __init__(self, name: str, params: dict) -> None:
        self.name = name
        self.params = params
        # A source can return useful records even when another page/company fails.
        # The pipeline reports these errors without discarding the successful records.
        self.errors: list[str] = []
        self.board_snapshots: list[dict[str, Any]] = []
        self._snapshot_filtered = False
        self._snapshot_auth_required = False

    @property
    def source_health(self) -> dict[str, Any]:
        statuses = {row["status"] for row in self.board_snapshots}
        status = (
            "not_checked" if not statuses else
            "success" if statuses == {"success"} else
            "auth_required" if statuses == {"auth_required"} else
            "failed" if statuses <= {"failed", "auth_required"} else "partial"
        )
        return {
            "source": self.name, "status": status,
            "snapshot_complete": bool(self.board_snapshots) and all(
                row["snapshot_complete"] for row in self.board_snapshots
            ),
            "snapshots": len(self.board_snapshots), "errors": list(self.errors),
        }

    @abstractmethod
    def fetch_jobs(self) -> list[JobRecord]:
        raise NotImplementedError
