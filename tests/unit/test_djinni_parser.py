"""Djinni parsing on saved fixtures: the RSS feed and the JobPosting JSON-LD of job pages."""

import json
from datetime import UTC, datetime

import pytest

from job_matcher.collectors.djinni import FeedItem, find_job_posting, parse_feed, parse_job
from job_matcher.models import Language, RemoteType, Seniority, Source

ITEM = FeedItem(
    url="https://djinni.co/jobs/1-qa/",
    title="QA Engineer",
    published_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
)


@pytest.fixture
def job_page(read_fixture):
    return lambda ident: read_fixture(f"djinni/job_{ident}.html").decode("utf-8")


def _job(**overrides):
    """A minimal JobPosting; each test overrides only the fields it is about."""
    data = {
        "@type": "JobPosting",
        "title": "QA Engineer",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocationType": "TELECOMMUTE",
        "description": "We test things with Playwright.",
    }
    return {**data, **overrides}


def _page(*blocks):
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    return f"<html><head>{scripts}</head></html>"


def test_feed_items(read_fixture):
    items = parse_feed(read_fixture("djinni/rss_qa_automation.xml"))

    assert len(items) == 6
    assert items[0] == FeedItem(
        url="https://djinni.co/jobs/855977-network-automation-engineer/",
        title="Network Automation Engineer",
        published_at=datetime.fromisoformat("2026-10-30T14:13:11+02:00"),
    )


def test_feed_item_without_a_link_is_skipped(caplog):
    xml = """<rss><channel>
        <item><title>QA</title><pubDate>Fri, 30 Oct 2026 14:13:11 +0200</pubDate></item>
        <item><title>QA Lead</title><link>https://djinni.co/jobs/2-qa-lead/</link>
              <pubDate>Fri, 30 Oct 2026 14:13:11 +0200</pubDate></item>
    </channel></rss>"""

    assert [i.title for i in parse_feed(xml)] == ["QA Lead"]
    assert "missing link, title or date" in caplog.text


def test_fixture_job_with_salary_and_region(job_page):
    v = parse_job(find_job_posting(job_page("855977")), ITEM)

    assert v.source is Source.DJINNI
    assert (v.title, v.company) == ("Network Automation Engineer", "Adaptiq")
    assert (v.location, v.remote) == ("Europe", RemoteType.YES)
    assert (v.salary_min, v.salary_max, v.currency) == (5500, 6500, "USD")
    assert v.seniority is Seniority.SENIOR, "5+ years required, no grade in the title"
    assert v.language_of_post is Language.EN
    assert v.published_at == ITEM.published_at, "the feed date, which has a timezone"
    assert v.description


@pytest.mark.parametrize(
    ("ident", "location"),
    [("853121", "Ukraine"), ("852016", None), ("855598", "Romania")],
    ids=["country-code", "no-requirements", "list-of-areas"],
)
def test_fixture_locations(job_page, ident, location):
    assert parse_job(find_job_posting(job_page(ident)), ITEM).location == location


@pytest.mark.parametrize(
    ("requirements", "expected"),
    [
        ({"address": {"addressRegion": "Europe", "addressCountry": None}}, "Europe"),
        ({"address": {"addressCountry": "POL"}}, "Poland"),
        ({"address": {"addressCountry": "PL"}}, "Poland"),
        ({"address": {"addressCountry": "XKX"}}, "XKX"),  # unknown code: kept as is
        ([{"address": {"addressCountry": "UA"}}, {"address": {"addressCountry": "PL"}}],
         "Ukraine, Poland"),
        ({"address": {}}, None),
    ],
    ids=["region", "alpha-3", "alpha-2", "unknown-code", "several", "empty"],
)  # fmt: skip
def test_location(requirements, expected):
    assert parse_job(_job(applicantLocationRequirements=requirements), ITEM).location == expected


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ({"unitText": "MONTH", "minValue": 4000, "maxValue": 5000}, "USD", (4000, 5000, "USD")),
        ({"unitText": "YEAR", "minValue": 60000, "maxValue": 72000}, "EUR", (5000, 6000, "EUR")),
        ({"unitText": "MONTH", "maxValue": 5000}, "USD", (None, 5000, "USD")),
        ({"unitText": "HOUR", "minValue": 30, "maxValue": 40}, "USD", (None, None, None)),
        ({"unitText": "MONTH"}, "USD", (None, None, None)),
        ({"unitText": "MONTH", "minValue": 4000}, None, (None, None, None)),
    ],
    ids=["monthly", "annual", "only-max", "hourly", "no-amount", "no-currency"],
)
def test_salary(value, currency, expected):
    v = parse_job(_job(baseSalary={"currency": currency, "value": value}), ITEM)

    assert (v.salary_min, v.salary_max, v.currency) == expected


@pytest.mark.parametrize(
    ("title", "months", "expected"),
    [
        ("Middle QA Engineer", 60, Seniority.MIDDLE),  # the title wins
        ("QA Engineer", 60, Seniority.SENIOR),
        ("QA Engineer", 36, None),
        ("QA Engineer", None, None),
    ],
    ids=["title-wins", "five-years", "three-years", "not-given"],
)
def test_seniority(title, months, expected):
    experience = {"monthsOfExperience": months} if months is not None else None
    v = parse_job(_job(title=title, experienceRequirements=experience), ITEM)

    assert v.seniority is expected


def test_not_telecommute_is_not_marked_remote():
    assert parse_job(_job(jobLocationType=None), ITEM).remote is None


def test_title_whitespace_and_company_registration_are_cleaned():
    v = parse_job(
        _job(
            title="Functional Tester  (Romania)",
            hiringOrganization={"name": "Acme Ltd. Company No. 123"},
        ),
        ITEM,
    )

    assert (v.title, v.company) == ("Functional Tester (Romania)", "Acme Ltd.")


def test_missing_title_falls_back_to_the_feed():
    assert parse_job(_job(title=None), ITEM).title == "QA Engineer"


def test_missing_company_is_an_error():
    with pytest.raises(KeyError):
        parse_job(_job(hiringOrganization={}), ITEM)


def test_job_posting_is_found_among_other_json_ld():
    page = _page("{broken", json.dumps({"@type": "BreadcrumbList"}), json.dumps(_job()))

    assert find_job_posting(page)["title"] == "QA Engineer"


@pytest.mark.parametrize(
    "page",
    [_page(json.dumps({"@type": "Organization"})), "<html></html>", _page("[1, 2]")],
    ids=["other-type", "no-json-ld", "not-an-object"],
)
def test_page_without_a_job_posting_is_an_error(page):
    with pytest.raises(ValueError, match="no JobPosting"):
        find_job_posting(page)
