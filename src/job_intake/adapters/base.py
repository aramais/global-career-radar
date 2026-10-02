from __future__ import annotations

from abc import ABC, abstractmethod

from job_intake.models.job import JobRecord


class JobSourceAdapter(ABC):
    def __init__(self, name: str, params: dict) -> None:
        self.name = name
        self.params = params
        # A source can return useful records even when another page/company fails.
        # The pipeline reports these errors without discarding the successful records.
        self.errors: list[str] = []

    @abstractmethod
    def fetch_jobs(self) -> list[JobRecord]:
        raise NotImplementedError
