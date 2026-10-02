"""Extract conservative vacancy excerpts from RFC822, without accessing the mailbox.

Email excerpts are leads, never proof that a vacancy is open or that its complete
description, location eligibility, or working language has been collected.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.text import compact_text, contains_any, stable_hash

MAX_EMAIL_BYTES = 2 * 1024 * 1024
MAX_MIME_PARTS = 64
MAX_MIME_DEPTH = 16
MAX_BODY_BYTES = 256 * 1024
MAX_LINKS = 1000
MAX_JOBS = 100
MAX_DESCRIPTION_CHARS = 6000

_ROLE = re.compile(
    r"\b(?:manager|director|analyst|engineer|scientist|developer|designer|"
    r"architect|researcher|consultant|specialist|coordinator|officer|"
    r"product\s+(?:owner|lead)|(?:analytics|data\s+science|engineering)\s+lead|"
    r"head\s+of\s+\w+|(?:chief|vp|vice\s+president)\s+\w+|"
    r"gerente|diretor|analista|cientista|engenheiro|coordenador|líder)\b",
    re.IGNORECASE,
)
_SERVICE = re.compile(
    r"unsubscribe|(?:email|notification|communication)\s+preferences|"
    r"manage\s+(?:your\s+)?(?:alerts|subscription)|privacy|terms\s+(?:of|and)|"
    r"view\s+(?:this\s+)?(?:email|message)|view\s+in\s+browser|"
    r"saved\s+search|job\s+alerts|jobs\s+(?:for|matching)|"
    r"(?:browse|search|all|more|new)\s+(?:remote\s+)?jobs|"
    r"(?:hiring|career)\s+(?:advice|tips)|resume\s+(?:tips|review)",
    re.IGNORECASE,
)
_EMAIL_ADDRESS = re.compile(r"\b[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+\b")
_MAIL_HEADER = re.compile(
    r"^(?:from|to|cc|bcc|subject|date|sent|message-id|reply-to):", re.IGNORECASE
)
_PERSONAL_LINE = re.compile(
    r"^(?:dear|hi|hello|привет|здравствуйте)\b|"
    r"\b(?:account|customer|membership)\s+(?:number|id)\b|"
    r"\byour\s+(?:personal\s+)?account\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_COMPANY_TITLE = re.compile(r"^(.+?)\s+(?:at|@)\s+(.+)$", re.IGNORECASE)
_LINKEDIN_JOB = re.compile(r"/(?:comm/)?jobs/view/(\d{1,30})/?$")
_GENERIC_LINK = re.compile(
    r"^(?:apply(?:\s+now)?|view\s+(?:job|details)|see\s+(?:job|details)|"
    r"job\s+details|details|learn\s+more|read\s+more|saiba\s+mais|ver\s+vaga|"
    r"candidatar-se|candidature-se|candidate-se|save\s+job)$",
    re.IGNORECASE,
)
_LOCATION_HINT = re.compile(
    r"🌎|🌍|🌏|\b(?:remote|worldwide|brazil|brasil|united\s+states|estados\s+unidos|"
    r"canada|canadá|mexico|méxico|argentina|united\s+kingdom|india|germany|"
    r"alemanha|portugal|spain|espanha|latam|europe|бразилия|сан[-‐‑– ]паулу|"
    r"гибридный\s+формат\s+работы|работа\s+в\s+офисе|удал[её]нная\s+работа)\b|"
    r",\s*[A-Z]{2}(?:\s|$)",
    re.IGNORECASE,
)
_WORK_MODES = (
    (re.compile(r"гибридный\s+формат\s+работы|\bhybrid\b", re.IGNORECASE), "Hybrid"),
    (re.compile(r"работа\s+в\s+офисе|\bon[ -]?site\b", re.IGNORECASE), "On-site"),
    (re.compile(r"удал[её]нная\s+работа|\bremote\b", re.IGNORECASE), "Remote"),
)
_JOB_PATH = re.compile(
    r"(?:^|[/_-])(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|"
    r"opportunit(?:y|ies)|recruitment|requisitions?)[/_-].+",
    re.IGNORECASE,
)
_NON_JOB_PATH = re.compile(
    r"(?:^|[/_-])(?:unsubscribe|preferences|privacy|terms|redirect|track|tracking|"
    r"pixel|click|search|login|signin|register|subscribe)(?:[/_.-]|$)",
    re.IGNORECASE,
)
_TRACKING_DOMAINS = (
    "sendgrid.net",
    "list-manage.com",
    "mailchimp.com",
    "mandrillapp.com",
    "sparkpostmail.com",
    "linkedinemail.com",
)
_ATS_DOMAINS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "jobs.smartrecruiters.com",
)
_VACANCY_QUERY_KEYS = {
    "job",
    "jobid",
    "job_id",
    "gh_jid",
    "position",
    "requisitionid",
    "reqid",
    "postingid",
    "vacancyid",
    "jid",
}
_REDIRECT_KEYS = {"url", "redirect", "redirect_uri", "target", "destination"}


def _bounded_bodies(raw: bytes) -> tuple[list[str], list[str], str | None]:
    if not isinstance(raw, bytes):
        raise TypeError("Email input must be bytes")
    if len(raw) > MAX_EMAIL_BYTES:
        raise ValueError("Email exceeds the supported size limit")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (ValueError, TypeError, LookupError, RecursionError):
        raise ValueError("Email could not be parsed") from None

    email_date = None
    try:
        value = message.get("Date")
        if value:
            parsed = parsedate_to_datetime(str(value))
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                email_date = parsed.isoformat()
    except (ValueError, TypeError, OverflowError):
        pass

    html_parts: list[str] = []
    plain_parts: list[str] = []
    pending: list[tuple[Message, int]] = [(message, 0)]
    parts_seen = 0
    body_bytes = 0
    while pending:
        part, depth = pending.pop()
        parts_seen += 1
        if parts_seen > MAX_MIME_PARTS or depth > MAX_MIME_DEPTH:
            raise ValueError("Email exceeds the supported MIME complexity limit")
        # Attached text and forwarded message attachments are not job email bodies.
        if part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type == "message/rfc822":
            continue
        if part.is_multipart():
            children = part.get_payload()
            if isinstance(children, list):
                pending.extend((child, depth + 1) for child in reversed(children))
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        body_bytes += len(payload)
        if body_bytes > MAX_BODY_BYTES:
            raise ValueError("Email exceeds the supported body size limit")
        try:
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, ValueError):
            body = payload.decode("utf-8", errors="replace")
        (html_parts if content_type == "text/html" else plain_parts).append(body)
    return html_parts, plain_parts, email_date


def _title_and_company(value: str, *, trusted_vacancy: bool = False) -> tuple[str, str] | None:
    value = compact_text(value)
    if (
        not 3 <= len(value) <= 180
        or _MAIL_HEADER.match(value)
        or _PERSONAL_LINE.search(value)
        or _SERVICE.search(value)
        or _GENERIC_LINK.fullmatch(value)
        or _URL.search(value)
    ):
        return None
    company = "Unknown"
    match = _COMPANY_TITLE.match(value)
    if match:
        value, company = map(compact_text, match.groups())
        if _MAIL_HEADER.match(company) or _SERVICE.search(company):
            return None
    if (
        _EMAIL_ADDRESS.search(value)
        or (not trusted_vacancy and not _ROLE.search(value))
        or len(value.split()) > 24
    ):
        return None
    if len(company) > 120 or _EMAIL_ADDRESS.search(company):
        company = "Unknown"
    return value, company


def _validated_url(value: str):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        return None
    if any(unicodedata.category(char).startswith("C") for char in unquote(value)):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    if (
        parsed.scheme.lower() not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char in hostname for char in "%\\/#?")
        or "\\" in unquote(parsed.path)
        or _EMAIL_ADDRESS.search(unquote(parsed.path))
    ):
        return None
    return parsed, hostname.casefold(), port


def _vacancy_url(value: str) -> str | None:
    validated = _validated_url(value)
    if not validated:
        return None
    parsed, hostname, port = validated
    query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
    if hostname.endswith(".safelinks.protection.outlook.com") and parsed.path == "/":
        targets = [target for key, target in query if key.casefold() == "url"]
        if len(targets) != 1:
            return None
        validated = _validated_url(targets[0])
        if not validated:
            return None
        parsed, hostname, port = validated
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)

    # These IDs identify a specific vacancy. Drop the personalized communication
    # path and every query token, rather than retaining recipient tracking data.
    linkedin_job = _LINKEDIN_JOB.fullmatch(parsed.path)
    if hostname in {"linkedin.com", "www.linkedin.com", "m.linkedin.com"} and linkedin_job:
        return f"https://www.linkedin.com/jobs/view/{linkedin_job[1]}"

    if (
        hostname.endswith(".safelinks.protection.outlook.com")
        or hostname.split(".")[0]
        in {"click", "trk", "track", "tracking", "links", "link", "email", "mail", "newsletter"}
        or any(
            hostname == domain or hostname.endswith("." + domain) for domain in _TRACKING_DOMAINS
        )
        or _NON_JOB_PATH.search(unquote(parsed.path))
        or parsed.path.casefold().endswith((".png", ".gif", ".jpg", ".jpeg", ".svg"))
        or any(key.casefold() in _REDIRECT_KEYS for key, _ in query)
    ):
        return None

    # Keep only documented vacancy identity fields. Unknown query keys can carry
    # recipient identifiers and must never become stored URLs or dedup identity.
    if any(
        key.casefold() in _VACANCY_QUERY_KEYS and _EMAIL_ADDRESS.search(unquote(val))
        for key, val in query
    ):
        return None
    query = [
        (key.casefold(), val)
        for key, val in query
        if key.casefold() in _VACANCY_QUERY_KEYS and val and not _EMAIL_ADDRESS.search(unquote(val))
    ]
    parts = [part for part in parsed.path.split("/") if part]
    job_query = bool(query)
    if not (
        _JOB_PATH.search(parsed.path) or job_query or (hostname in _ATS_DOMAINS and len(parts) >= 2)
    ):
        return None

    netloc = hostname
    if ":" in hostname:  # IPv6, if supplied: rendering must preserve brackets.
        netloc = f"[{hostname}]"
    if port is not None and port != (443 if parsed.scheme.lower() == "https" else 80):
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), urlencode(sorted(query)), "")
    )


def _snippet(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = compact_text(line)
        if (
            not line
            or _MAIL_HEADER.match(line)
            or _EMAIL_ADDRESS.search(line)
            or _PERSONAL_LINE.search(line)
        ):
            continue
        if _SERVICE.search(line) or line.casefold().startswith("copyright"):
            continue
        line = _URL.sub("", line)
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)[:MAX_DESCRIPTION_CHARS]


def _card(anchor: Tag, candidates: dict[int, tuple[str, str, str]]) -> Tag:
    """Stop before an ancestor containing another job or newsletter boilerplate."""
    vacancy_url = candidates[id(anchor)][0]
    # LinkedIn wraps a multi-row card in an outer anchor, while its shorter title
    # is another anchor inside that wrapper. Keep the card, not just the title row.
    for ancestor in [anchor, *list(anchor.parents)[:8]]:
        if not isinstance(ancestor, Tag) or ancestor.name in {"body", "html", "[document]"}:
            break
        if ancestor.name != "a" or not ancestor.get("href"):
            continue
        if _vacancy_url(ancestor["href"]) != vacancy_url:
            continue
        text = ancestor.get_text("\n", strip=True)
        urls = {
            candidates[id(link)][0]
            for link in ancestor.find_all("a", href=True)
            if id(link) in candidates
        }
        leaf_metadata = any(
            not leaf.find(["div", "p", "table", "a"])
            and "·" in leaf.get_text()
            and _LOCATION_HINT.search(leaf.get_text())
            for leaf in ancestor.find_all("div", recursive=False, limit=12)
        )
        if (
            (ancestor.find(["p", "table"]) or leaf_metadata)
            and len(text) <= MAX_DESCRIPTION_CHARS
            and not _SERVICE.search(text)
            and not (urls - {vacancy_url})
        ):
            return ancestor
    fallback = anchor
    for ancestor in list(anchor.parents)[:8]:
        if not isinstance(ancestor, Tag) or ancestor.name in {"body", "html", "[document]"}:
            break
        text = ancestor.get_text("\n", strip=True)
        if len(text) > MAX_DESCRIPTION_CHARS or _SERVICE.search(text):
            break
        urls = {
            candidates[id(link)][0]
            for link in ancestor.find_all("a", href=True)
            if id(link) in candidates
        }
        if len(urls) > 1:
            break
        if ancestor.name in {"h1", "h2", "h3", "h4", "p"}:
            fallback = ancestor
        classes = " ".join(ancestor.get("class", []))
        if ancestor.name in {"article", "li", "tr"} or re.search(
            r"(?:^|[\s_-])(?:card|vacancy|listing)(?:[\s_-]|$)", classes, re.IGNORECASE
        ):
            return ancestor
    # An unmarked wrapper can be the entire one-job newsletter. For unknown
    # layouts, the identified title/heading is safer than collecting that wrapper.
    return fallback


def _anchor_title(anchor: Tag) -> str:
    for heading in anchor.find_all(["h1", "h2", "h3", "h4", "span"], limit=20):
        value = compact_text(heading.get_text(" ", strip=True))
        if _title_and_company(value):
            return value
    # DailyRemote also renders title + metadata as sibling leaf divs inside the
    # vacancy link. Only inspect those leaves, never an enclosing digest wrapper.
    for leaf in anchor.find_all("div", limit=20):
        if leaf.find(["div", "p", "table", "a"]):
            continue
        value = compact_text(leaf.get_text(" ", strip=True))
        if _title_and_company(value):
            return value
    return anchor.get_text(" ", strip=True)


def _location_and_mode(value: str) -> tuple[str | None, str | None]:
    remote = None
    for pattern, mode in _WORK_MODES:
        if pattern.search(value):
            remote = mode
            value = pattern.sub("", value)
            break
    value = re.sub(r"\(\s*\)", "", value)
    value = re.sub(r"\bбразилия\b", "Brazil", value, flags=re.IGNORECASE)
    value = re.sub(r"\bсан[-‐‑– ]паулу\b", "São Paulo", value, flags=re.IGNORECASE)
    location = compact_text(value).strip("🌎🌍🌏 ,·-")
    return location or None, remote


def _company_value(value: str) -> str | None:
    if any(_MAIL_HEADER.match(line.strip()) for line in value.splitlines()):
        return None
    value = compact_text(value)
    if not 1 <= len(value) <= 120 or _EMAIL_ADDRESS.search(value) or _SERVICE.search(value):
        return None
    return value


def _card_details(block: Tag, company: str) -> tuple[str, str | None, str | None, str | None]:
    location = salary = remote = None
    if company == "Unknown":
        company_node = block.select_one(".company, .company-name, [data-company]")
        if company_node:
            value = _company_value(
                company_node.get("data-company") or company_node.get_text(" ", strip=True)
            )
            if value:
                company = value
    metadata_nodes = block.find_all("p", limit=12)
    if block.name == "a":
        metadata_nodes.extend(
            leaf
            for leaf in block.find_all("div", recursive=False, limit=12)
            if not leaf.find(["div", "p", "table", "a"])
        )
    for paragraph in metadata_nodes:
        value = compact_text(paragraph.get_text(" ", strip=True))
        if (
            len(value) > 350
            or _EMAIL_ADDRESS.search(value)
            or _MAIL_HEADER.match(value)
            or _SERVICE.search(value)
            or _PERSONAL_LINE.search(value)
        ):
            continue
        fields = [compact_text(field) for field in value.split("·")]
        if (
            not 2 <= len(fields) <= 4
            or not 1 <= len(fields[0]) <= 120
            or _ROLE.search(fields[0])
            or not _LOCATION_HINT.search(fields[1])
        ):
            continue
        company_value = _company_value(fields[0])
        if not company_value:
            continue
        if company == "Unknown":
            company = company_value
        location, remote = _location_and_mode(fields[1])
        for field in fields[2:]:
            if re.search(r"[$€£]|\b(?:USD|EUR|BRL)\b", field):
                salary = field.strip("💵 ")
        break
    return company, location, salary, remote


def _html_jobs(body: str) -> list[tuple[str, str, str, str, str | None, str | None, str | None]]:
    soup = BeautifulSoup(body, "html.parser")
    for node in soup.select("script, style, noscript, iframe, form, footer, header, nav"):
        node.decompose()
    candidates: dict[int, tuple[str, str, str]] = {}
    anchors = soup.find_all("a", href=True, limit=MAX_LINKS + 1)
    if len(anchors) > MAX_LINKS:
        raise ValueError("Email exceeds the supported link count limit")
    for anchor in anchors:
        url = _vacancy_url(anchor["href"])
        title = _title_and_company(
            _anchor_title(anchor),
            trusted_vacancy=bool(url and url.startswith("https://www.linkedin.com/jobs/view/")),
        )
        if url and title:
            candidates[id(anchor)] = (url, *title)
    jobs = []
    # If a company and title link share a vacancy ID, favor the recognizable role.
    anchors.sort(
        key=lambda anchor: (
            not bool(_ROLE.search(candidates.get(id(anchor), ("", "", ""))[1])),
            len(candidates.get(id(anchor), ("", "", ""))[1]),
        )
    )
    for anchor in anchors:
        candidate = candidates.get(id(anchor))
        if not candidate:
            continue
        if any(
            id(child) in candidates and candidates[id(child)][0] == candidate[0]
            for child in anchor.find_all("a", href=True)
        ):
            continue
        block = _card(anchor, candidates)
        url, title, company = candidate
        company, location, salary, remote = _card_details(block, company)
        jobs.append(
            (
                url,
                title,
                company,
                _snippet(block.get_text("\n", strip=True)) or title,
                location,
                salary,
                remote,
            )
        )
    return jobs


def _plain_jobs(body: str) -> list[tuple[str, str, str, str, str | None, str | None, str | None]]:
    lines = body.splitlines()
    jobs = []
    for index, line in enumerate(lines):
        matches = list(_URL.finditer(line))
        if len(matches) != 1:
            continue
        match = matches[0]
        url = _vacancy_url(match.group().rstrip(".,;!)]}"))
        if not url:
            continue
        title_index = index
        trusted_vacancy = url.startswith("https://www.linkedin.com/jobs/view/")
        title = _title_and_company(
            line[: match.start()].strip(" -•"), trusted_vacancy=trusted_vacancy
        )
        if title is None and index > 0:
            title_index = index - 1
            title = _title_and_company(
                lines[title_index].strip(" -•"), trusted_vacancy=trusted_vacancy
            )
        if title is None:
            continue
        excerpt = [lines[title_index]]
        for following in lines[index + 1 : index + 7]:
            if not following.strip() or _URL.search(following) or _title_and_company(following):
                break
            excerpt.append(following)
        jobs.append((url, *title, _snippet("\n".join(excerpt)) or title[0], None, None, None))
    return jobs


def parse_email_jobs(
    raw: bytes,
    source: str,
    message_ref: str,
    *,
    keywords: list[str] | None = None,
) -> list[JobRecord]:
    """Return bounded, deduplicated vacancy excerpts, ignoring headers and attachments.

    HTML cards with title links and plain text titles immediately preceding URLs
    are supported. Unknown tracking redirects and unrecognized layouts are skipped.
    ``email_date`` is the sender's message date, deliberately separate from posted_at.
    """
    html_parts, plain_parts, email_date = _bounded_bodies(raw)
    try:
        candidates = [candidate for body in html_parts for candidate in _html_jobs(body)]
        if not candidates:
            candidates = [candidate for body in plain_parts for candidate in _plain_jobs(body)]
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Email body could not be parsed within the supported limits") from None
    jobs = []
    seen: set[str] = set()
    for url, title, company, description, location, salary, remote in candidates:
        if url in seen or (keywords and not contains_any(title, keywords)):
            continue
        seen.add(url)
        metadata = {
            "message_ref": compact_text(message_ref)[:256],
            "description_complete": False,
            "description_source": "email_excerpt",
        }
        if email_date:
            metadata["email_date"] = email_date
        jobs.append(
            JobRecord(
                source=source,
                company=company,
                title=title,
                original_url=url,
                apply_url=url,
                source_job_id="email-url:" + stable_hash(url),
                posted_at=None,
                location_text=location,
                salary_text=salary,
                remote_text=remote,
                description_raw=description,
                description_clean=description,
                status=JobStatus.UNKNOWN,
                source_metadata=metadata,
            )
        )
        if len(jobs) >= MAX_JOBS:
            break
    return jobs
