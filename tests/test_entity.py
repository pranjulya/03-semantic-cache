import json

import pytest

from scache.entity import entities_compatible, entity_diff, extract_entities, load_products
from make_eval import ROOT

PRODUCTS = {"Trailhead Pack 40L": "TP-40", "Trailhead Pack 65L": "TP-65", "Summit Tent 2P": "ST-2",
            "Summit Tent 4P": "ST-4", "Beacon Headlamp": "BH-1"}


def ents(text):
    return extract_entities(text, PRODUCTS)


def compat(a, b):
    return entities_compatible(a, b, PRODUCTS)


# ---- extraction, one test group per entity type

@pytest.mark.parametrize("text,expected", [
    ("Is shipping free on a $80 order?", {"usd:80"}),
    ("Is shipping free on a $ 80 order?", {"usd:80"}),
    ("It costs $1,299.50 in total", {"usd:1299.5"}),
    ("Can I pay 80 dollars?", {"usd:80"}),
    ("Can I pay eighty dollars?", set()),  # number words: only digits and a small list are understood
    ("What does a $12.00 repair cost?", {"usd:12"}),
])
def test_money(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Code gives 10 percent off", {"pct:10"}),
    ("Tax is 5.5%", {"pct:5.5"}),
    ("a 15 per cent fee", {"pct:15"}),
])
def test_percent(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("return within 30 days", {"dur:43200min"}),
    ("a 30-day window", {"dur:43200min"}),
    ("after one year", {"dur:1year"}),
    ("2 weeks later", {"dur:20160min"}),
    ("cancel within 90 minutes", {"dur:90min"}),
    ("cancel within 1.5 hours", {"dur:90min"}),
    ("arrives in 5 to 7 business days", {"dur:5-7bday"}),
    ("takes 5 business days", {"dur:5bday"}),
    ("within 3 months", {"dur:3month"}),
])
def test_duration(text, expected):
    assert ents(text) == expected


def test_equal_durations_in_different_units_match():
    assert ents("within 24 hours") == ents("within 1 day")
    assert ents("within 3 days") != ents("within 3 business days")


@pytest.mark.parametrize("text,expected", [
    ("ships before 2 pm", {"time:14:00"}),
    ("opens at 9 am", {"time:09:00"}),
    ("closes at 4:30pm", {"time:16:30"}),
    ("open at 12 am", {"time:00:00"}),
    ("open at 12 pm", {"time:12:00"}),
    ("is noon too late", {"time:12:00"}),
    ("at 14:00 sharp", {"time:14:00"}),
    ("I am sure", set()),
])
def test_time_of_day(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("open on December 24?", {"date:12-24"}),
    ("open on Dec 24th?", {"date:12-24"}),
    ("open on the 24th of December?", {"date:12-24"}),
    ("open on 24 December?", {"date:12-24"}),
    ("open on July 4", {"date:07-04"}),
    ("in December we are busy", {"month:12"}),
    ("May I return this?", set()),
    ("open on May 5", {"date:05-05"}),
    ("Christmas Eve hours", {"date:12-24"}),
    ("Is Christmas Day a holiday", {"date:12-25"}),
    ("New Year's Day", {"date:01-01"}),
    ("closed on Thanksgiving Day", {"hol:thanksgiving"}),
    ("open on Black Friday", {"hol:blackfriday"}),
    ("open the day after Thanksgiving", {"hol:blackfriday"}),
])
def test_dates_and_holidays(text, expected):
    assert ents(text) == expected


def test_holiday_and_numeric_forms_match():
    assert ents("open on Christmas Eve?") == ents("open on December 24?")
    assert ents("open on the day after Thanksgiving?") == ents("open on Black Friday?")


@pytest.mark.parametrize("text,expected", [
    ("open on Sunday", {"day:sun"}),
    ("open on Sundays", {"day:sun"}),
    ("phone support on Saturday", {"day:sat"}),
    ("weekday hours", {"range:weekday"}),
    ("weekend hours", {"range:weekend"}),
])
def test_weekdays(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Does code SPRING10 work?", {"id:SPRING10"}),
    ("Does the coupon spring10 work?", {"id:SPRING10"}),
    ("Is SPRING10 valid?", {"id:SPRING10"}),
    ("Where is order FO-482913?", {"id:FO-482913"}),
    ("Which product has SKU RB-1?", {"id:RB-1"}),  # RB-1 is not in this test's product table
    ("What is the code for the sale?", set()),
])
def test_identifiers(text, expected):
    assert ents(text) == expected


def test_sku_and_product_name_are_the_same_entity():
    assert ents("What is the price of ST-2?") == ents("What is the price of the Summit Tent 2P?") == {"prod:summit tent 2p"}


@pytest.mark.parametrize("text,expected", [
    ("price of the Trailhead Pack 40L", {"prod:trailhead pack 40l"}),
    ("price of the trailhead  pack 40l", {"prod:trailhead pack 40l"}),
    ("Does the Beacon Headlamp work?", {"prod:beacon headlamp"}),
    ("Is a headlamp returnable?", set()),
    ("Is a Summit tent returnable?", set()),
])
def test_products(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("price of the Summit Tent 6P", {"model:6p"}),
    ("a 2nd item", set()),
    ("the 25th", set()),  # a lone ordinal is not an entity; "December 25th" is a date
])
def test_unlisted_model_codes(text, expected):
    assert ents(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("What can I redeem 250 points for?", {"num:250"}),
    ("Will it arrive in 6 days?", {"dur:8640min"}),
    ("Call 207-555-0142 for support", {"tel:2075550142"}),
    ("Is there a store on Main Street?", set()),
    ("5.5 points", {"num:5.5"}),
])
def test_bare_numbers_and_phone(text, expected):
    assert ents(text) == expected


def test_no_double_counting_between_types():
    assert ents("Open at 9 am on December 24 for 2 hours at $5") == {"time:09:00", "date:12-24", "dur:120min", "usd:5"}


# ---- compatibility

def test_compatible_when_same_entities_in_different_words():
    assert compat("How much is shipping on a $80 order?", "What does delivery cost for an order of $80?")
    assert compat("Can I return an item 30 days after delivery?", "Is a 30 day return still possible?")
    assert compat("What is the return policy?", "How do returns work?")


@pytest.mark.parametrize("a,b", [
    ("Can I return an item 20 days after delivery?", "Can I return an item 40 days after delivery?"),
    ("Is shipping free on a $74 order?", "Is shipping free on a $76 order?"),
    ("Does SPRING10 work?", "Does TRAIL20 work?"),
    ("Price of the Summit Tent 2P?", "Price of the Summit Tent 4P?"),
    ("Price of ST-2?", "Price of ST-4?"),
    ("Open on December 24?", "Open on December 25?"),
    ("Open on Thanksgiving Day?", "Open on Black Friday?"),
    ("Open on Saturday?", "Open on Sunday?"),
    ("Ships if ordered at 1 pm?", "Ships if ordered at 4 pm?"),
    ("Does a 10 percent code work?", "Does a 20 percent code work?"),
    ("Price of the Summit Tent 6P?", "Price of the Summit Tent 8P?"),
    ("Arrives within 5 business days?", "Arrives within 5 days?"),
])
def test_incompatible_when_an_entity_differs(a, b):
    assert not compat(a, b)


def test_incompatible_when_only_one_question_has_an_entity():
    assert not compat("Can I return an item?", "Can I return an item 40 days after delivery?")
    assert not compat("Can I return a headlamp?", "Can I return the Beacon Headlamp?")


def test_entity_diff_reports_each_side():
    assert entity_diff("Open on Sunday at 10 am?", "Open on Sunday at 4 pm?", PRODUCTS) == ({"time:10:00"}, {"time:16:00"})
    assert entity_diff("Open Sunday?", "Is it open on Sunday?", PRODUCTS) == (set(), set())


def test_compat_is_symmetric():
    a, b = "Open at 9 am on Sunday?", "Open on Sunday?"
    assert compat(a, b) == compat(b, a)


def test_load_products_reads_policy_table():
    text = "| Product | SKU | Price |\n|---|---|---|\n| Trailhead Pack 40L | TP-40 | $129 |\n| Beacon Headlamp | BH-1 | $59 |\n"
    assert load_products(text) == {"Trailhead Pack 40L": "TP-40", "Beacon Headlamp": "BH-1"}
    real = load_products((ROOT / "data" / "policy.md").read_text())
    assert len(real) == 7 and real["Summit Tent 2P"] == "ST-2"


# ---- Set A regression (these pairs were in view while the guard was written: a regression test, not a measurement)

def test_guard_rejects_every_near_miss_pair_and_keeps_most_paraphrases():
    pairs = [json.loads(l) for l in (ROOT / "eval" / "pairs.jsonl").read_text().splitlines()]
    near = [p for p in pairs if p["type"] == "near_miss"]
    para = [p for p in pairs if p["type"] == "paraphrase"]
    assert [p["id"] for p in near if entities_compatible(p["q1"], p["q2"])] == []
    assert len([p for p in para if not entities_compatible(p["q1"], p["q2"])]) <= 2


# ---- known gaps: the guard does not see these. strict xfail so a fix is noticed.
# (Not a gap: a family name vs a specific product is rejected, see the test above.)

@pytest.mark.xfail(strict=True, reason="number words are only understood before a unit or 'dollars'")
def test_gap_number_words_alone():
    assert not compat("Can I use two promo codes?", "Can I use three promo codes?")


def test_product_family_vs_specific_product_is_conservatively_incompatible():
    assert not compat("What is the Summit tent warranty?", "What is the warranty on the Summit Tent 4P?")


@pytest.mark.xfail(strict=True, reason="abbreviated weekdays are not recognised")
def test_gap_abbreviated_weekdays():
    assert not compat("Is the store open on Sat?", "Is the store open on Sun?")


@pytest.mark.xfail(strict=True, reason="a different intent with no differing entity is not the guard's job")
def test_gap_different_intent_same_entities():
    assert not compat("How long is the warranty on the Compass GPS Watch?", "How much is the Compass GPS Watch?")
