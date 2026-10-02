from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from job_intake.adapters._email_parsing import parse_email_jobs
from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord
from job_intake.utils.text import canonicalize_url

MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class EmailFilesAdapter(JobSourceAdapter):
    """Read explicitly supplied local messages without connecting to a mailbox."""

    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        directory, max_messages, keywords = self._validated_params()
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except (OSError, ValueError):
            raise ValueError("Email directory must exist and be a readable directory") from None

        jobs: list[JobRecord] = []
        seen_urls: set[str] = set()
        seen_source_ids: set[str] = set()
        try:
            try:
                names = sorted(name for name in os.listdir(directory_fd) if name.endswith(".eml"))
            except OSError:
                raise ValueError("Email directory must exist and be a readable directory") from None
            candidates: list[str] = []
            for name in names:
                try:
                    file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    self.errors.append("Email file: unable to inspect message")
                    continue
                if stat.S_ISREG(file_stat.st_mode):
                    candidates.append(name)
            if len(candidates) > max_messages:
                self.errors.append("Email message limit reached; additional files were skipped")
            for position, name in enumerate(candidates[:max_messages], start=1):
                try:
                    raw = self._read_message(directory_fd, name)
                except (OSError, ValueError):
                    self.errors.append(f"Email file {position}: unable to read message")
                    continue
                if len(raw) > MAX_MESSAGE_BYTES:
                    self.errors.append(f"Email file {position}: message exceeds 2 MiB limit")
                    continue
                message_ref = hashlib.sha256(raw).hexdigest()
                try:
                    parsed = parse_email_jobs(raw, self.name, message_ref, keywords=keywords)
                except Exception:
                    self.errors.append(f"Email file {position}: unable to parse message")
                    continue
                for job in parsed:
                    url = canonicalize_url(job.apply_url or job.original_url)
                    source_id = job.source_job_id
                    if (url and url in seen_urls) or (source_id and source_id in seen_source_ids):
                        continue
                    if url:
                        seen_urls.add(url)
                    if source_id:
                        seen_source_ids.add(source_id)
                    jobs.append(job)
        finally:
            os.close(directory_fd)
        return jobs

    def _validated_params(self) -> tuple[Path, int, list[str] | None]:
        raw_directory = self.params.get("directory")
        if not isinstance(raw_directory, (str, Path)) or not str(raw_directory).strip():
            raise ValueError("Email directory is required")
        directory = Path(raw_directory)
        raw_limit = self.params.get("max_messages", 100)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, str)):
            raise ValueError("max_messages must be between 1 and 1000")
        try:
            max_messages = int(raw_limit)
        except ValueError:
            raise ValueError("max_messages must be between 1 and 1000") from None
        if not 1 <= max_messages <= 1000:
            raise ValueError("max_messages must be between 1 and 1000")
        keywords = self.params.get("keywords")
        if keywords is not None and (
            not isinstance(keywords, list)
            or any(not isinstance(word, str) or not word.strip() for word in keywords)
        ):
            raise ValueError("Email keywords must be a list of nonempty strings")
        return directory, max_messages, keywords

    @staticmethod
    def _read_message(directory_fd: int, name: str) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Email message must be a regular file")
            return handle.read(MAX_MESSAGE_BYTES + 1)
