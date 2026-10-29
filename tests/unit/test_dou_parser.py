"""DOU parsing, offline: real titles and saved RSS fixtures, no network."""

import logging

import pytest
from defusedxml import DefusedXmlException

from job_matcher.collectors.dou import TitleParts, parse_feed, parse_title
from job_matcher.models import Language, RemoteType, Seniority, Source

# Titles are taken from real DOU feeds (after XML decoding, entities still double-escaped).
TITLE_CASES = [
    (
        "Senior QA Automation Engineer, ASIC Simulation в NVIDIA, Київ, віддалено",
        TitleParts(
            "Senior QA Automation Engineer, ASIC Simulation", "NVIDIA", ("Київ",), remote=True
        ),
    ),
    (
        "Head of QA (in Warsaw) в Synergetica, Варшава (Польща)",
        TitleParts("Head of QA (in Warsaw)", "Synergetica", ("Варшава (Польща)",)),
    ),
    (
        "Manual QA Engineer в Mavericks Agency, $600\u20131000, віддалено",  # en dash, as on DOU
        TitleParts(
            "Manual QA Engineer", "Mavericks Agency", remote=True, salary_min=600, salary_max=1000
        ),
    ),
    (
        "Senior Manual QA Engineer в Interactive Online Technologies, від&nbsp;$2700, "
        "за кордоном, віддалено",
        TitleParts(
            "Senior Manual QA Engineer",
            "Interactive Online Technologies",
            ("за кордоном",),
            remote=True,
            salary_min=2700,
        ),
    ),
    (
        "Application Security Engineer / Web &amp; API Penetration Tester "
        "в HighCraft.io, віддалено",
        TitleParts(
            "Application Security Engineer / Web & API Penetration Tester",
            "HighCraft.io",
            remote=True,
        ),
    ),
    ("QA Engineer в Acme, до $5000", TitleParts("QA Engineer", "Acme", salary_max=5000)),
    (
        "QA Engineer в Acme, $4000",
        TitleParts("QA Engineer", "Acme", salary_min=4000, salary_max=4000),
    ),
    ("QA Engineer в Kendoo, Europe", TitleParts("QA Engineer", "Kendoo", ("Europe",))),
    ("QA Engineer в Acme", TitleParts("QA Engineer", "Acme")),
    ("QA Engineer в Acme, Київ, ", TitleParts("QA Engineer", "Acme", ("Київ",))),
]


@pytest.mark.parametrize(
    ("title", "expected"),
    TITLE_CASES,
    ids=[
        "comma-in-position",
        "on-site-no-remote",
        "salary-range",
        "salary-from-nbsp",
        "html-entity",
        "salary-up-to",
        "salary-exact",
        "latin-location",
        "company-only",
        "trailing-comma",
    ],
)
def test_parse_title(title, expected):
    assert parse_title(title) == expected


@pytest.mark.parametrize("title", ["QA Engineer at Acme", " в Acme", ""])
def test_parse_title_rejects_unknown_format(title):
    with pytest.raises(ValueError, match="unexpected DOU title format"):
        parse_title(title)


@pytest.mark.parametrize("name", ["dou/qa_remote.xml", "dou/qa_relocation.xml"])
def test_fixture_feed_parses_every_item(read_fixture, caplog, name):
    vacancies = parse_feed(read_fixture(name))

    assert len(vacancies) == 25
    assert not caplog.records, "no item should be skipped"
    for v in vacancies:
        assert v.source is Source.DOU
        assert "utm_" not in str(v.url)
        assert v.published_at is not None
        assert v.published_at.tzinfo is not None


def _find(vacancies, company, title):
    return next(v for v in vacancies if v.company == company and v.title == title)


def test_fixture_vacancy_fields(read_fixture):
    vacancies = parse_feed(read_fixture("dou/qa_relocation.xml"))

    v = _find(vacancies, "Techstack Ltd", "Senior QA Automation Engineer")
    assert v.seniority is Seniority.SENIOR
    assert v.remote is RemoteType.YES
    assert "Вроцлав (Польща)" in v.location
    assert v.language_of_post is Language.EN
    assert v.requires_polish is False
    assert v.salary_min is None
    assert v.currency is None


def test_fixture_salary_is_usd(read_fixture):
    vacancies = parse_feed(read_fixture("dou/qa_remote.xml"))

    v = _find(vacancies, "Mavericks Agency", "Manual QA Engineer")
    assert (v.salary_min, v.salary_max, v.currency) == (600, 1000, "USD")


def _rss(*items: str) -> str:
    return f"<rss><channel>{''.join(items)}</channel></rss>"


def _item(title="QA Engineer в Acme", link="https://jobs.dou.ua/vacancies/1/", description="x"):
    return (
        f"<item><title>{title}</title><link>{link}</link>"
        f"<description>{description}</description>"
        "<pubDate>Thu, 29 Oct 2026 15:10:59 +0200</pubDate></item>"
    )


@pytest.mark.parametrize(
    "broken",
    [
        _item(title="No company here"),
        _item(link="not-a-url"),
        "<item><title>QA в Acme</title><link>https://jobs.dou.ua/vacancies/2/</link></item>",
    ],
    ids=["bad-title", "bad-url", "missing-fields"],
)
def test_broken_item_is_skipped_not_fatal(caplog, broken):
    caplog.set_level(logging.WARNING)

    vacancies = parse_feed(_rss(_item(), broken))

    assert [str(v.url) for v in vacancies] == ["https://jobs.dou.ua/vacancies/1/"]
    assert "skipped item" in caplog.text


def test_entity_expansion_attack_is_rejected():
    """'Billion laughs': a tiny XML that expands to gigabytes. defusedxml must refuse it."""
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        "<rss><channel><item><title>&b;</title></item></channel></rss>"
    )
    with pytest.raises(DefusedXmlException):
        parse_feed(bomb)
