from __future__ import annotations

from abc import ABC, abstractmethod

from job_intake.models.job import JobRecord


class JobSourceAdapter(ABC):
    def __init__(self, name: str, params: dict) -> None:
        self.name = name
        self.params = params

    @abstractmethod
    def fetch_jobs(self) -> list[JobRecord]:
        raise NotImplementedError
