from __future__ import annotations

from email.message import EmailMessage
from urllib.parse import quote

import pytest

from job_intake.adapters._email_parsing import (
    MAX_BODY_BYTES,
    MAX_EMAIL_BYTES,
    parse_email_jobs,
)
from job_intake.models.job import JobStatus
from job_intake.storage.dedup import JobDeduplicator


def message(body: str, *, html: bool = True, date: str | None = None) -> bytes:
    email = EmailMessage()
    email["From"] = "Private Sender <private.sender@example.com>"
    email["To"] = "Personal Name <personal.name@example.com>"
    email["Subject"] = "Private subject: personal account and saved job search"
    if date:
        email["Date"] = date
    email.set_content(body, subtype="html" if html else "plain")
    return email.as_bytes()


def parse(raw: bytes, **kwargs):
    return parse_email_jobs(raw, "email_files", "message-sha", **kwargs)


def test_html_digest_yields_two_separate_vacancy_excerpts() -> None:
    jobs = parse(
        message("""
      <div>Dear Personal Name, here are your job recommendations.</div>
      <article><h2><a href="https://example.com/jobs/123?utm_source=email">
        Product Manager at Acme</a></h2>
        <p>Own the roadmap and run experiments.</p></article>
      <article><h2><a href="https://example.com/jobs/456">Head of Analytics</a></h2>
        <p class="company">Other Corp</p><p>Manage a team of analysts.</p></article>
      <footer><a href="https://example.com/unsubscribe">Unsubscribe</a>
        Personal Name personal.name@example.com</footer>
    """)
    )
    assert [(job.title, job.company) for job in jobs] == [
        ("Product Manager", "Acme"),
        ("Head of Analytics", "Other Corp"),
    ]
    assert "Own the roadmap" in jobs[0].description_clean
    assert "Manage a team" not in jobs[0].description_clean
    assert "roadmap" not in jobs[1].description_clean
    assert "Personal Name" not in str(jobs)
    assert "personal.name" not in str(jobs)
    assert "private.sender" not in str(jobs)
    assert "Private subject" not in str(jobs)
    assert jobs[0].original_url == "https://example.com/jobs/123"


def test_plain_role_immediately_before_url() -> None:
    jobs = parse(
        message(
            "Product Manager at Acme\nhttps://example.com/jobs/123\n"
            "Own product strategy and experiments.\n\n"
            "Data Science Manager\nhttps://example.com/jobs/456\nManage scientists.\n",
            html=False,
        )
    )
    assert len(jobs) == 2
    assert jobs[0].title == "Product Manager"
    assert jobs[0].company == "Acme"
    assert "product strategy" in jobs[0].description_clean
    assert "Manage scientists" not in jobs[0].description_clean
    assert jobs[1].company == "Unknown"


def test_plain_inline_url_does_not_remain_in_description() -> None:
    job = parse(
        message(
            "Product Manager at Acme - https://example.com/jobs/123\nOwn the roadmap.", html=False
        )
    )[0]
    assert job.title == "Product Manager"
    assert "https" not in job.description_clean


def test_email_date_does_not_become_job_posting_date() -> None:
    job = parse(
        message(
            '<a href="https://example.com/jobs/123">Product Manager</a>',
            date="Fri, 02 Oct 2026 16:35:00 -0300",
        )
    )[0]
    assert job.status is JobStatus.UNKNOWN
    assert job.posted_at is None
    assert job.location_text is None
    assert job.remote_text is None
    assert job.source_metadata == {
        "message_ref": "message-sha",
        "email_date": "2026-10-02T16:35:00-03:00",
        "description_complete": False,
        "description_source": "email_excerpt",
    }


def test_email_text_is_not_evidence_of_complete_jd_or_global_eligibility() -> None:
    job = parse(
        message("""
      <p>Fully remote roles worldwide with English working language!</p>
      <article><a href="https://example.com/jobs/123">Product Manager</a>
      <p>Own the roadmap.</p></article>
    """)
    )[0]
    assert job.company == "Unknown"
    assert job.remote_text is None
    assert job.location_text is None
    assert job.source_metadata["description_complete"] is False
    assert "worldwide" not in job.description_clean


@pytest.mark.parametrize(
    "title",
    [
        "View job",
        "Apply now",
        "Jobs for Product Managers",
        "Product Manager job alerts",
        "Manage your alerts",
        "Privacy policy",
        "Find all remote jobs",
        "private.name@example.com",
        "",
        "Acme",
    ],
)
def test_non_titles_do_not_create_job_records(title: str) -> None:
    assert parse(message(f'<a href="https://example.com/jobs/123">{title}</a>')) == []


def test_digest_subject_does_not_become_title_for_bare_links() -> None:
    email = EmailMessage()
    email["Subject"] = "Product Manager jobs for you"
    email.set_content("Here are your recommendations\nhttps://example.com/jobs/123")
    assert parse(email.as_bytes()) == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///tmp/jobs/123",
        "https://user:secret@example.com/jobs/123",
        "https://example.com/jobs/123%0Aprivate",
        "https://example.com/jobs/123%00",
        "https://example.com/jobs/123%5Cprivate",
        "https://example.com/privacy/jobs/123",
        "https://example.com/unsubscribe/jobs/123",
        "https://example.com/jobs/pixel.png",
        "https://click.example.com/jobs/123",
        "https://example.com/redirect/jobs/123",
        "https://example.com/jobs/123?url=https%3A%2F%2Fexample.com%2Fjobs%2F456",
        "https://abc.sendgrid.net/jobs/123",
        "https://example.com/",
        "https://example.com/jobs",
    ],
)
def test_service_unsafe_and_unknown_redirect_urls_are_skipped(url: str) -> None:
    assert parse(message(f'<a href="{url}">Product Manager</a>')) == []


def test_duplicate_links_and_new_email_keep_stable_vacancy_identity() -> None:
    first = parse(
        message("""
      <article><a href="https://example.com/jobs/123?utm_source=first&ref=email">
        Product Manager at Acme</a><p>Own the roadmap.</p>
        <a href="https://example.com/jobs/123?utm_source=second">Product Manager</a></article>
    """)
    )
    second = parse_email_jobs(
        message("""
      <article><a href="https://EXAMPLE.com/jobs/123/?utm_source=next&ref=email">
        Product Manager at Acme</a><p>Updated description.</p></article>
    """),
        "email_files",
        "different-message",
    )
    assert len(first) == len(second) == 1
    assert first[0].source_job_id == second[0].source_job_id
    dedup = JobDeduplicator()
    assert dedup.build_identity(first[0]).job_uid == dedup.build_identity(second[0]).job_uid
    assert first[0].source_metadata["message_ref"] != second[0].source_metadata["message_ref"]


def test_unknown_tracking_query_is_removed_from_generic_vacancy_urls() -> None:
    job = parse(
        message(
            '<a href="https://example.com/jobs/123?recipient_id=private&amp;uid=private'
            '&amp;refId=private&amp;lipi=private&amp;newTrackingField=private">'
            "Product Manager</a>"
        )
    )[0]
    assert job.original_url == "https://example.com/jobs/123"
    assert job.apply_url == job.original_url
    assert "private" not in str(job)


def test_query_only_vacancy_identifier_is_preserved_without_unknown_tracking() -> None:
    job = parse(
        message(
            '<a href="https://example.com/?gh_jid=123&amp;recipient_id=private">Product Manager</a>'
        )
    )[0]
    assert job.original_url == "https://example.com?gh_jid=123"
    assert "private" not in str(job)


def test_new_tracking_query_does_not_change_vacancy_source_identity() -> None:
    first = parse(
        message(
            '<a href="https://example.com/jobs/123?recipient_id=first&amp;unknown_id=one">'
            "Product Manager</a>"
        )
    )[0]
    second = parse_email_jobs(
        message(
            '<a href="https://example.com/jobs/123?uid=second&amp;new_personal_field=two">'
            "Product Manager</a>"
        ),
        "email_files",
        "new-message",
    )[0]
    assert first.source_job_id == second.source_job_id
    assert JobDeduplicator().build_identity(first).job_uid == (
        JobDeduplicator().build_identity(second).job_uid
    )


def test_unknown_query_only_tracking_is_not_a_vacancy_identifier() -> None:
    assert (
        parse(
            message(
                '<a href="https://example.com/?uid=private&amp;recipient_id=private">'
                "Product Manager</a>"
            )
        )
        == []
    )


def test_keyword_filter_uses_role_title_not_other_newsletter_jobs() -> None:
    jobs = parse(
        message("""
      <article><a href="https://example.com/jobs/123">Product Manager</a>
      <p>Work closely with the Head of Analytics.</p></article>
      <article><a href="https://example.com/jobs/456">Head of Analytics</a></article>
    """),
        keywords=["Head of Analytics"],
    )
    assert [job.title for job in jobs] == ["Head of Analytics"]


def test_multipart_alternative_prefers_html_without_duplicate_plain_record() -> None:
    email = EmailMessage()
    email.set_content("Product Manager at Acme\nhttps://example.com/jobs/123")
    email.add_alternative(
        '<article><a href="https://example.com/jobs/123">Product Manager at Acme</a>'
        "<p>HTML card excerpt.</p></article>",
        subtype="html",
    )
    jobs = parse(email.as_bytes())
    assert len(jobs) == 1
    assert "HTML card excerpt" in jobs[0].description_clean


def test_html_without_recognizable_jobs_can_fall_back_to_plain_alternative() -> None:
    email = EmailMessage()
    email.set_content("Product Manager at Acme\nhttps://example.com/jobs/123")
    email.add_alternative(
        "<div>Your email client must load external content.</div>", subtype="html"
    )
    assert len(parse(email.as_bytes())) == 1


def test_attachments_are_not_parsed_as_vacancies() -> None:
    email = EmailMessage()
    email.set_content("Nothing to import.")
    email.add_attachment(
        '<a href="https://example.com/jobs/123">Product Manager</a>',
        subtype="html",
        filename="private-export.html",
    )
    email.add_attachment(
        "Data Science Manager\nhttps://example.com/jobs/456",
        subtype="plain",
        filename="private.txt",
    )
    assert parse(email.as_bytes()) == []


def test_charset_and_scripts_are_handled_without_rendering_html() -> None:
    email = EmailMessage()
    email.set_content(
        '<article><a href="https://example.com/jobs/123">Product Manager at Café</a>'
        "<p>Estratégia e análise.</p><script>privateSecret()</script>"
        "<style>.private {color:red}</style></article>",
        subtype="html",
        charset="iso-8859-1",
    )
    job = parse(email.as_bytes())[0]
    assert job.company == "Café"
    assert "Estratégia" in job.description_clean
    assert "privateSecret" not in str(job)
    assert "color:red" not in str(job)
    assert "<article" not in job.description_raw


def test_unknown_charset_falls_back_without_leaking_encoding_header() -> None:
    raw = (
        b"Content-Type: text/plain; charset=private-secret-encoding\n\n"
        b"Product Manager\nhttps://example.com/jobs/123"
    )
    assert parse(raw)[0].title == "Product Manager"


def test_known_safelink_unwraps_once_and_drops_recipient_tracking() -> None:
    target = "https://example.com/jobs/123?utm_source=email&email=person%40example.com&jobid=123"
    url = "https://nam01.safelinks.protection.outlook.com/?url=" + quote(target, safe="")
    job = parse(message(f'<a href="{url}">Product Manager</a>'))[0]
    assert job.original_url == "https://example.com/jobs/123?jobid=123"
    assert "person" not in str(job)


def test_nested_safelinks_and_unsafe_targets_are_not_unwrapped() -> None:
    for target in [
        "https://nam01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fjobs%2F123",
        "https://user:secret@example.com/jobs/123",
        "javascript:alert(1)",
    ]:
        url = "https://nam01.safelinks.protection.outlook.com/?url=" + quote(target, safe="")
        assert parse(message(f'<a href="{url}">Product Manager</a>')) == []


def test_body_header_lines_and_contact_details_are_not_saved() -> None:
    jobs = parse(
        message("""
      <article><a href="https://example.com/jobs/123">Product Manager</a>
        <p>Own the roadmap.</p><p>From: private sender</p><p>To: private recipient</p>
        <p>Contact personal.name@example.com for details.</p></article>
    """)
    )
    assert "private sender" not in str(jobs)
    assert "private recipient" not in str(jobs)
    assert "personal.name" not in str(jobs)
    assert "Own the roadmap" in jobs[0].description_clean


@pytest.mark.parametrize(
    "company_markup",
    [
        "<p>From: Synthetic Private Sender · Brazil</p>",
        "<p class='company'>From: Synthetic Private Sender</p>",
        "<p data-company='From: Synthetic Private Sender'></p>",
        "<p class='company'>To: Synthetic Private Recipient</p>",
        "<p>Manage your alerts · Brazil</p>",
    ],
)
def test_body_headers_and_service_text_do_not_become_company_metadata(
    company_markup: str,
) -> None:
    job = parse(
        message(
            '<article><a href="https://example.com/jobs/123">Product Manager</a>'
            + company_markup
            + "<p>Own the roadmap.</p></article>"
        )
    )[0]
    assert job.company == "Unknown"
    assert job.location_text is None
    assert "Synthetic Private" not in str(job)


@pytest.mark.parametrize(
    "title",
    [
        "From: Synthetic Private Product Manager",
        "To: Product Manager",
        "Product Manager at From: Synthetic Private Sender",
    ],
)
def test_body_header_titles_do_not_become_job_records(title: str) -> None:
    assert parse(message(f'<a href="https://example.com/jobs/123">{title}</a>')) == []


def test_linkedin_communication_urls_strip_all_private_query_tokens() -> None:
    job = parse(
        message("""
      <article><a href="https://www.linkedin.com/comm/jobs/view/12345?trackingId=PRIVATE&amp;refId=REF&amp;lipi=TOKEN">
        Data Science Manager - Portfólio de Crédito</a><p>Own credit models.</p></article>
    """)
    )[0]
    assert job.original_url == "https://www.linkedin.com/jobs/view/12345"
    assert "PRIVATE" not in str(job)
    assert "TOKEN" not in str(job)
    assert job.posted_at is None
    assert job.status is JobStatus.UNKNOWN


@pytest.mark.parametrize("title", ["Gerente de Dados II Senior", "Methodology Lead"])
def test_trusted_linkedin_vacancy_id_supports_unknown_language_or_role(title: str) -> None:
    job = parse(message(f'<a href="https://www.linkedin.com/comm/jobs/view/123">{title}</a>'))[0]
    assert job.title == title
    assert job.company == "Unknown"


@pytest.mark.parametrize("title", ["View job", "Apply", "Ver vaga", "Privacy", ""])
def test_even_trusted_linkedin_id_needs_a_title(title: str) -> None:
    assert parse(message(f'<a href="https://www.linkedin.com/jobs/view/123">{title}</a>')) == []


def test_linkedin_normal_and_communication_urls_deduplicate_across_emails() -> None:
    first = parse(
        message(
            '<a href="https://www.linkedin.com/comm/jobs/view/123?trackingId=secret">'
            "Product Manager</a>"
        )
    )[0]
    second = parse_email_jobs(
        message('<a href="https://www.linkedin.com/jobs/view/123">Product Manager</a>'),
        "email_files",
        "new-message",
    )[0]
    assert first.source_job_id == second.source_job_id


def test_nested_linkedin_anchors_keep_real_title_and_individual_card_metadata() -> None:
    jobs = parse(
        message("""
      <div>Dear Personal Name, here are your recommendations.</div>
      <a href="https://www.linkedin.com/comm/jobs/view/123/"><img src="tracker.gif"></a>
      <a href="https://www.linkedin.com/comm/jobs/view/123/?trackingId=PRIVATE">
        <table><tbody><tr><td>
          <a href="https://www.linkedin.com/comm/jobs/view/123/">
            Data Science Manager - Portfólio de Crédito</a>
        </td></tr><tr><td><p>Example Bank · Осаску, SP</p></td></tr>
        <tr><td><p>Активный набор персонала</p></td></tr></tbody></table>
      </a>
      <a href="https://www.linkedin.com/comm/jobs/view/456/">
        <table><tbody><tr><td>
          <a href="https://www.linkedin.com/comm/jobs/view/456/">Gerente de Dados II Senior</a>
        </td></tr><tr><td><p>Other Bank · São Paulo, SP</p></td></tr>
        <tr><td><p>Work with data engineers.</p></td></tr></tbody></table>
      </a>
      <p>Manage your alerts · personal.name@example.com</p>
    """)
    )
    assert len(jobs) == 2
    by_url = {job.original_url: job for job in jobs}
    first = by_url["https://www.linkedin.com/jobs/view/123"]
    assert first.title == "Data Science Manager - Portfólio de Crédito"
    assert first.company == "Example Bank"
    assert first.location_text == "Осаску, SP"
    assert "Активный набор" in first.description_clean
    assert "Other Bank" not in first.description_clean
    assert "Personal Name" not in str(jobs)
    assert "PRIVATE" not in str(jobs)
    assert first.status is JobStatus.UNKNOWN
    second = by_url["https://www.linkedin.com/jobs/view/456"]
    assert second.title == "Gerente de Dados II Senior"
    assert second.company == "Other Bank"
    assert second.location_text == "São Paulo, SP"


def test_dailyremote_span_link_uses_title_and_explicit_country_salary() -> None:
    job = parse(
        message("""
      <div><table><tr><td>
        <a href="https://dailyremote.com/remote-job/product-owner-123">
          <span>Lead Product Owner - Data Analytics</span>
          <p>FERGUSON · 🌎 United States · 💵 $101K - $178K per year</p>
        </a>
      </td></tr></table><p>Manage your alerts</p></div>
    """)
    )[0]
    assert job.title == "Lead Product Owner - Data Analytics"
    assert job.company == "FERGUSON"
    assert job.location_text == "United States"
    assert job.salary_text == "$101K - $178K per year"
    assert job.remote_text is None
    assert job.status is JobStatus.UNKNOWN
    assert job.source_metadata["description_complete"] is False


def test_dailyremote_sibling_leaf_divs_keep_role_and_explicit_metadata_inside_anchor() -> None:
    job = parse(
        message("""
      <div><p>Dear Synthetic Private Recipient</p>
        <table><tr><td>
        <a href="https://dailyremote.com/remote-job/lead-product-owner-data-analytics-123?utm_source=job_alert">
          <div>Lead Product Owner - Data Analytics</div>
          <div>ExampleCorp &middot; 🌎 United States &middot; 💵 $101K - $178K per year</div>
        </a>
        </td></tr></table>
        <p>Your account number is private-number.</p>
      </div>
    """)
    )[0]
    assert job.title == "Lead Product Owner - Data Analytics"
    assert job.company == "ExampleCorp"
    assert job.location_text == "United States"
    assert job.salary_text == "$101K - $178K per year"
    assert job.original_url == (
        "https://dailyremote.com/remote-job/lead-product-owner-data-analytics-123"
    )
    assert job.remote_text is None
    assert job.status is JobStatus.UNKNOWN
    assert job.source_metadata["description_complete"] is False
    assert "Synthetic Private" not in str(job)
    assert "private-number" not in str(job)


def test_dot_separated_role_skills_do_not_become_company_and_location() -> None:
    job = parse(
        message("""
      <article><a href="https://example.com/jobs/123">Product Manager</a>
      <p>Roadmap · Experiments · Strategy</p></article>
    """)
    )[0]
    assert job.company == "Unknown"
    assert job.location_text is None


@pytest.mark.parametrize(
    ("explicit", "location", "mode"),
    [
        ("Сан-Паулу, Бразилия (Гибридный формат работы)", "São Paulo, Brazil", "Hybrid"),
        ("Осаску, SP (Работа в офисе)", "Осаску, SP", "On-site"),
        ("Бразилия (Удаленная работа)", "Brazil", "Remote"),
        ("Бразилия (Удалённая работа)", "Brazil", "Remote"),
        ("United States (Remote)", "United States", "Remote"),
        ("Remote", None, "Remote"),
        ("Brazil", "Brazil", None),
    ],
)
def test_location_work_mode_comes_only_from_explicit_card_metadata(
    explicit: str, location: str | None, mode: str | None
) -> None:
    job = parse(
        message(f"""
      <article><a href="https://www.linkedin.com/comm/jobs/view/123">Product Manager</a>
        <p>Example Bank · {explicit}</p><p>Own the roadmap.</p></article>
    """)
    )[0]
    assert job.company == "Example Bank"
    assert job.location_text == location
    assert job.remote_text == mode
    assert explicit in job.description_clean
    assert job.status is JobStatus.UNKNOWN
    assert job.source_metadata["description_complete"] is False


def test_remote_or_brazil_in_other_paragraph_is_not_location_evidence() -> None:
    job = parse(
        message("""
      <article><a href="https://www.linkedin.com/jobs/view/123">Product Manager</a>
        <p>Work with remote colleagues in Brazil.</p></article>
    """)
    )[0]
    assert job.location_text is None
    assert job.remote_text is None


def test_single_job_card_does_not_widen_into_personalized_newsletter() -> None:
    job = parse(
        message("""
      <div><p>Dear Personal Name, we selected these jobs for your career.</p>
      <div><a href="https://example.com/jobs/123">Product Manager</a>
      <p>Own the roadmap.</p></div><p>Your personal account number is private-number.</p></div>
    """)
    )[0]
    assert job.description_clean == "Product Manager"
    assert "Personal Name" not in str(job)
    assert "private-number" not in str(job)


def test_unknown_flat_layout_keeps_only_identified_role_not_personalized_body() -> None:
    job = parse(
        message("""
      <div><p>Dear Synthetic Private Recipient</p>
      <a href="https://example.com/jobs/123">Product Manager</a>
      <p>Own the roadmap.</p><p>Your account number is private-number.</p></div>
    """)
    )[0]
    assert job.description_clean == "Product Manager"
    assert "Synthetic Private" not in str(job)
    assert "private-number" not in str(job)


def test_obvious_personal_boilerplate_is_removed_even_from_known_card() -> None:
    job = parse(
        message("""
      <article><a href="https://example.com/jobs/123">Product Manager</a>
      <p>Dear Synthetic Private Recipient</p><p>Own the roadmap.</p>
      <p>Your account number is private-number.</p></article>
    """)
    )[0]
    assert "Own the roadmap" in job.description_clean
    assert "Synthetic Private" not in str(job)
    assert "private-number" not in str(job)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/jobs/123/private.recipient%40example.org",
        "https://example.com/jobs/123/private.recipient@example.org",
        "https://example.com/jobs/123?job_id=private.recipient%40example.org",
        "https://example.com/?gh_jid=private.recipient%40example.org",
    ],
)
def test_recipient_addresses_in_path_or_vacancy_identifier_are_not_saved(url: str) -> None:
    assert parse(message(f'<a href="{url}">Product Manager</a>')) == []


def test_table_cards_do_not_include_other_rows_or_footer() -> None:
    jobs = parse(
        message("""
      <table><tr><td>Dear Personal Name</td></tr>
      <tr><td><h3><a href="https://example.com/jobs/123">Product Manager</a></h3>
      <p>Roadmap and discovery.</p></td></tr>
      <tr><td><h3><a href="https://example.com/jobs/456">Analytics Manager</a></h3>
      <p>Manage analysts.</p></td></tr>
      <tr><td>Unsubscribe personal.name@example.com</td></tr></table>
    """)
    )
    assert len(jobs) == 2
    assert "Personal Name" not in str(jobs)
    assert "Manage analysts" not in jobs[0].description_clean


def test_oversized_inputs_have_constant_errors_without_email_content() -> None:
    with pytest.raises(ValueError, match="supported size limit") as error:
        parse(b"private secret" + b"x" * MAX_EMAIL_BYTES)
    assert "private" not in str(error.value)
    with pytest.raises(ValueError, match="body size limit"):
        parse(message("x" * (MAX_BODY_BYTES + 1), html=False))


def test_excessive_mime_parts_are_rejected_without_header_content() -> None:
    email = EmailMessage()
    email.make_mixed()
    for _ in range(65):
        part = EmailMessage()
        part.set_content("private-secret")
        email.attach(part)
    with pytest.raises(ValueError, match="MIME complexity limit") as error:
        parse(email.as_bytes())
    assert "private-secret" not in str(error.value)


def test_invalid_query_does_not_leak_original_url_in_error() -> None:
    url = "https://example.com/jobs/123?" + "&".join(f"key{x}=private" for x in range(101))
    with pytest.raises(ValueError, match="supported limits") as error:
        parse(message(f'<a href="{url}">Product Manager</a>'))
    assert "private" not in str(error.value)
