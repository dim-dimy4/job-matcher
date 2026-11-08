import pytest

from job_matcher.models import Language, Seniority
from job_matcher.normalize import (
    clean_company,
    clean_url,
    detect_language,
    detect_seniority,
    html_to_text,
    required_years,
    requires_polish,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://jobs.dou.ua/companies/acme/vacancies/1/?utm_source=jobsrss",
            "https://jobs.dou.ua/companies/acme/vacancies/1/",
        ),
        (
            "https://example.com/job?id=7&utm_medium=rss&utm_campaign=x",
            "https://example.com/job?id=7",
        ),
        ("https://example.com/job#apply", "https://example.com/job"),
        ("  https://example.com/job  ", "https://example.com/job"),
    ],
    ids=["utm-only", "keeps-real-params", "drops-fragment", "strips-spaces"],
)
def test_clean_url(raw, expected):
    assert clean_url(raw) == expected


def test_html_to_text_strips_tags_and_entities():
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p>\n<ul><li>A &amp; B</li></ul>") == (
        "Hello world A & B"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior QA Automation Engineer", Seniority.SENIOR),
        ("Sr. QA Engineer", Seniority.SENIOR),
        ("Expert Automation QA Engineer (4720)", Seniority.SENIOR),
        ("QA Lead", Seniority.LEAD),
        ("Head of QA (in Warsaw)", Seniority.LEAD),
        ("Middle/Senior AQA", Seniority.SENIOR),  # highest grade wins
        ("Senior/Lead General QA Engineer", Seniority.LEAD),
        ("Junior+ / Middle Manual QA Engineer", Seniority.MIDDLE),
        ("Strong Junior QA Manual", Seniority.JUNIOR),
        ("QA Engineer (Intern)", Seniority.JUNIOR),
        ("Mobile QA Engineer (Middle)", Seniority.MIDDLE),
        ("QA Automation Engineer (Mobile)", None),
        ("Leading QA Engineer", None),  # word boundary: "Leading" is not "Lead"
    ],
)
def test_detect_seniority(title, expected):
    assert detect_seniority(title) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We are looking for a QA engineer with Playwright experience.", Language.EN),
        ("Шукаємо QA інженера з досвідом автоматизації тестування.", Language.UK),
        ("Szukamy testera z doświadczeniem w automatyzacji, praca zdalna.", Language.PL),
        ("Office in Wrocław, but the whole team speaks English every day.", Language.EN),
        ("", None),
        ("2026 / 100%", None),
    ],
    ids=["en", "uk", "pl", "en-with-polish-city", "empty", "no-letters"],
)
def test_detect_language(text, expected):
    assert detect_language(text) is expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Requirements: English B2, Polish C1.", Language.EN, True),
        ("Fluent Polish and English are required.", Language.EN, True),
        ("Native Polish speaker.", Language.EN, True),
        ("Polish at C1 level.", Language.EN, True),
        ("Bardzo dobra znajomość języka polskiego.", Language.EN, True),
        ("Any text at all.", Language.PL, True),  # a post written in Polish implies Polish
        ("Polish B1 is a plus.", Language.EN, False),
        ("Fluent English. Polish is nice to have.", Language.EN, False),
        ("Office in Poland, Polish team.", Language.EN, False),
        ("Шукаємо QA інженера.", Language.UK, False),
    ],
)
def test_requires_polish(text, language, expected):
    assert requires_polish(text, language) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Codest Ltd. Company No. 12590542, VAT number: GB363431020", "Codest Ltd."),
        ("Acme sp. z o.o., NIP 1234567890", "Acme sp. z o.o."),
        ("Acme GmbH; Registration number HRB 123", "Acme GmbH"),
        ("Vatican Tours", "Vatican Tours"),  # "vat" only as a whole word
        ("ELEKS", "ELEKS"),
    ],
    ids=["company-no-and-vat", "nip", "registration-number", "word-inside-name", "plain"],
)
def test_clean_company(name, expected):
    assert clean_company(name) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5+ years of experience in QA", 5),
        ("Requirements: 7+ years in QA and test automation", 7),
        ("6-7+ years of hands-on experience", 7),  # a range counts by its upper bound
        ("5\N{EN DASH}7 years of experience", 7),
        ("Досвід роботи від 3 років", 3),
        ("Опыт от 7 лет", 7),
        ("Doświadczenie: 4 lat", 4),
        ("2+ years of experience with Python; 4+ years of experience in QA", 4),
        ("We have been on the market for 20 years", None),  # not about the candidate
        ("A 10-year-old product, experience with APIs", None),
        ("Experience: 30 years", None),  # implausible
        ("", None),
    ],
    ids=["plus", "requirements", "range", "en-dash-range", "uk", "ru", "pl", "highest-wins",
         "company-age", "not-years-of", "implausible", "empty"],
)  # fmt: skip
def test_required_years(text, expected):
    assert required_years(text) == expected
