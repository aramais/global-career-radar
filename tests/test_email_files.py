"""Local email transport never needs mailbox access or network requests."""

import hashlib
import json
import os
from email.message import EmailMessage

import pytest
import requests

from job_intake.adapters import email_files
from job_intake.adapters.email_files import MAX_MESSAGE_BYTES, EmailFilesAdapter
from job_intake.models.job import JobRecord


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Local email import must not use the network")

    monkeypatch.setattr(requests.Session, "request", blocked)


def record(url="https://example.com/jobs/1", source_id=None, title="Product Manager"):
    return JobRecord(
        source="emailfile",
        company="Acme",
        title=title,
        original_url=url,
        source_job_id=source_id,
    )


def adapter(directory, **params):
    return EmailFilesAdapter("emailfile", {"directory": directory, **params})


@pytest.mark.parametrize("directory", [None, "", " ", 123, []])
def test_email_directory_is_required_without_exposing_input(directory):
    with pytest.raises(ValueError, match="Email directory is required"):
        adapter(directory).fetch_jobs()


def test_email_directory_missing_file_and_symlink_are_generic_errors(tmp_path):
    ordinary_file = tmp_path / "confidential-subject.eml"
    ordinary_file.write_bytes(b"private message")
    directory_link = tmp_path / "link"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    for path in [tmp_path / "missing-private-folder", ordinary_file, directory_link]:
        with pytest.raises(ValueError) as error:
            adapter(path).fetch_jobs()
        assert str(error.value) == "Email directory must exist and be a readable directory"
        assert str(path) not in str(error.value)


def test_email_invalid_directory_path_is_a_generic_error():
    with pytest.raises(ValueError) as error:
        adapter("private\x00directory").fetch_jobs()
    assert str(error.value) == "Email directory must exist and be a readable directory"


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5, None, "private-token"])
def test_email_limit_validation_is_bounded_and_generic(tmp_path, limit):
    with pytest.raises(ValueError) as error:
        adapter(tmp_path, max_messages=limit).fetch_jobs()
    assert str(error.value) == "max_messages must be between 1 and 1000"


@pytest.mark.parametrize("keywords", ["analytics", [None], [""], [" "]])
def test_email_keywords_validation(tmp_path, keywords):
    with pytest.raises(ValueError, match="Email keywords must be a list of nonempty strings"):
        adapter(tmp_path, keywords=keywords).fetch_jobs()


def test_email_empty_directory_is_successful(monkeypatch, tmp_path):
    monkeypatch.setattr(
        email_files, "parse_email_jobs", lambda *args, **kwargs: pytest.fail("No message exists")
    )
    source = adapter(tmp_path)
    assert source.fetch_jobs() == []
    assert source.errors == []


def test_email_reads_only_direct_regular_eml_files(monkeypatch, tmp_path):
    expected = b"Subject: Product Manager\n\nhttps://example.com/jobs/1"
    (tmp_path / "selected.eml").write_bytes(expected)
    (tmp_path / "notes.txt").write_bytes(b"ignored")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "nested.eml").write_bytes(b"ignored")
    (tmp_path / "directory.eml").mkdir()
    (tmp_path / "shortcut.eml").symlink_to(tmp_path / "selected.eml")
    os.mkfifo(tmp_path / "pipe.eml")
    calls = []

    def parse(raw, source, message_ref, *, keywords=None):
        calls.append((raw, source, message_ref, keywords))
        return [record()]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    source = adapter(tmp_path)
    assert len(source.fetch_jobs()) == 1
    assert calls == [(expected, "emailfile", hashlib.sha256(expected).hexdigest(), None)]
    assert source.errors == []


def test_email_order_and_provenance_are_deterministic_without_filenames(monkeypatch, tmp_path):
    (tmp_path / "z-private-subject.eml").write_bytes(b"second")
    (tmp_path / "a-private-subject.eml").write_bytes(b"first")
    calls = []

    def parse(raw, source, message_ref, *, keywords=None):
        calls.append((raw, message_ref, keywords))
        return [record(f"https://example.com/jobs/{raw.decode()}")]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    jobs = adapter(tmp_path, keywords=["Product Manager", "Analytics"]).fetch_jobs()
    assert [job.original_url for job in jobs] == [
        "https://example.com/jobs/first",
        "https://example.com/jobs/second",
    ]
    assert calls == [
        (raw, hashlib.sha256(raw).hexdigest(), ["Product Manager", "Analytics"])
        for raw in [b"first", b"second"]
    ]
    assert all("private" not in message_ref for _, message_ref, _ in calls)


def test_email_limit_skips_later_files_without_losing_first_results(monkeypatch, tmp_path):
    for index in range(3):
        (tmp_path / f"{index}.eml").write_bytes(str(index).encode())
    calls = []

    def parse(raw, *args, **kwargs):
        calls.append(raw)
        return [record(f"https://example.com/jobs/{raw.decode()}")]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    source = adapter(tmp_path, max_messages="2")
    assert len(source.fetch_jobs()) == 2
    assert calls == [b"0", b"1"]
    assert source.errors == ["Email message limit reached; additional files were skipped"]


def test_email_default_limit_is_100(monkeypatch, tmp_path):
    for index in range(101):
        (tmp_path / f"{index:03}.eml").write_bytes(b"message")
    calls = []
    monkeypatch.setattr(
        email_files, "parse_email_jobs", lambda raw, *args, **kwargs: calls.append(raw) or []
    )
    source = adapter(tmp_path)
    assert source.fetch_jobs() == []
    assert len(calls) == 100
    assert "limit reached" in source.errors[0]


def test_email_size_limit_is_inclusive_and_retains_good_messages(monkeypatch, tmp_path):
    (tmp_path / "a-oversize.eml").write_bytes(b"x" * (MAX_MESSAGE_BYTES + 1))
    (tmp_path / "b-boundary.eml").write_bytes(b"x" * MAX_MESSAGE_BYTES)
    (tmp_path / "c-small.eml").write_bytes(b"message")
    sizes = []

    def parse(raw, *args, **kwargs):
        sizes.append(len(raw))
        return [record(f"https://example.com/jobs/{len(raw)}")]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    source = adapter(tmp_path)
    assert len(source.fetch_jobs()) == 2
    assert sizes == [MAX_MESSAGE_BYTES, len(b"message")]
    assert source.errors == ["Email file 1: message exceeds 2 MiB limit"]


def test_email_parser_error_is_sanitized_and_later_success_survives(monkeypatch, tmp_path):
    private_filename = "a-confidential-salary.eml"
    (tmp_path / private_filename).write_bytes(b"private@example.com private body")
    (tmp_path / "b-good.eml").write_bytes(b"good")

    def parse(raw, *args, **kwargs):
        if raw.startswith(b"private"):
            raise ValueError(f"{tmp_path}/{private_filename}: {raw!r}")
        return [record()]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    source = adapter(tmp_path)
    assert len(source.fetch_jobs()) == 1
    assert source.errors == ["Email file 1: unable to parse message"]
    (tmp_path / private_filename).unlink()
    assert len(source.fetch_jobs()) == 1
    assert source.errors == []


def test_email_read_failure_is_sanitized_and_other_message_survives(monkeypatch, tmp_path):
    (tmp_path / "a-confidential.eml").write_bytes(b"first")
    (tmp_path / "b-good.eml").write_bytes(b"second")
    original = EmailFilesAdapter._read_message

    def read(directory_fd, name):
        if name.startswith("a-"):
            raise PermissionError(f"Permission denied for {tmp_path / name}")
        return original(directory_fd, name)

    monkeypatch.setattr(EmailFilesAdapter, "_read_message", staticmethod(read))
    monkeypatch.setattr(email_files, "parse_email_jobs", lambda *args, **kwargs: [record()])
    source = adapter(tmp_path)
    assert len(source.fetch_jobs()) == 1
    assert source.errors == ["Email file 1: unable to read message"]


def test_email_directory_listing_failure_is_generic(monkeypatch, tmp_path):
    def listdir(directory_fd):
        raise PermissionError(f"Private path: {tmp_path}")

    monkeypatch.setattr(email_files.os, "listdir", listdir)
    with pytest.raises(ValueError) as error:
        adapter(tmp_path).fetch_jobs()
    assert str(error.value) == "Email directory must exist and be a readable directory"


def test_email_deduplicates_by_canonical_url_and_source_id(monkeypatch, tmp_path):
    (tmp_path / "first.eml").write_bytes(b"first")
    (tmp_path / "second.eml").write_bytes(b"second")

    def parse(raw, *args, **kwargs):
        if raw == b"first":
            return [
                record("https://example.com/jobs/1?utm_source=first", "first-id"),
                record("https://example.com/jobs/2", "second-id"),
            ]
        return [
            record("https://EXAMPLE.com/jobs/1/?utm_source=second", "other-id"),
            record("https://example.com/jobs/changed", "second-id"),
            record("https://example.com/jobs/3", "third-id"),
        ]

    monkeypatch.setattr(email_files, "parse_email_jobs", parse)
    jobs = adapter(tmp_path).fetch_jobs()
    assert [job.source_job_id for job in jobs] == ["first-id", "second-id", "third-id"]


def test_email_symlink_swap_after_inspection_is_not_followed(monkeypatch, tmp_path):
    selected = tmp_path / "selected.eml"
    selected.write_bytes(b"original")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_bytes(b"should never be read")
    original_stat = os.stat

    def swap_after_stat(path, *, dir_fd=None, follow_symlinks=True):
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        selected.unlink()
        selected.symlink_to(unrelated)
        return result

    monkeypatch.setattr(email_files.os, "stat", swap_after_stat)
    monkeypatch.setattr(
        email_files, "parse_email_jobs", lambda *args, **kwargs: pytest.fail("Symlink was read")
    )
    source = adapter(tmp_path)
    assert source.fetch_jobs() == []
    assert source.errors == ["Email file 1: unable to read message"]


def test_email_files_parse_a_synthetic_gmail_export(tmp_path):
    message = EmailMessage()
    message["Subject"] = "Your new job alert"
    message["From"] = "alerts@sender.example"
    message["To"] = "personal@private.example"
    message["Date"] = "Fri, 02 Oct 2026 09:00:00 -0300"
    message.set_content("Please view the HTML job alert.")
    message.add_alternative(
        "<article><h2><a href='https://dailyremote.com/remote-job/product-manager-123'>"
        "Product Manager at Acme</a></h2><p>Remote in Brazil. English required.</p>"
        "<p>Own the roadmap and experimentation.</p></article>",
        subtype="html",
    )
    raw = message.as_bytes()
    (tmp_path / "confidential-subject.eml").write_bytes(raw)
    source = adapter(tmp_path, keywords=["Product Manager"])
    jobs = source.fetch_jobs()
    assert len(jobs) == 1
    assert jobs[0].source == "emailfile"
    assert jobs[0].title == "Product Manager"
    assert jobs[0].company == "Acme"
    assert jobs[0].original_url == "https://dailyremote.com/remote-job/product-manager-123"
    assert "Remote in Brazil" in jobs[0].description_clean
    assert jobs[0].posted_at is None
    assert jobs[0].source_metadata["description_complete"] is False
    metadata = json.dumps(jobs[0].source_metadata)
    assert hashlib.sha256(raw).hexdigest() in metadata
    assert "personal@private.example" not in metadata
    assert "alerts@sender.example" not in metadata
    assert "confidential-subject" not in metadata
    assert source.errors == []
