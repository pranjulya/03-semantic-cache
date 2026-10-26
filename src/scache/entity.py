"""Entity guard (M4). Embeddings barely notice "$60" vs "$76" or "Pack 40L" vs "Pack 65L"; code does.

extract_entities() returns normalised strings, one per entity mention, each prefixed by its type:
  usd:80  pct:10  time:14:00  date:12-24  month:12  hol:thanksgiving  day:sun (also Sun, Sat.)  range:weekday
  dur:1440min / dur:5-7bday / dur:2year  id:SPRING10  prod:<policy product>  model:6p  tel:2075550142  num:250 (also "two")
Two questions are compatible only if they mention exactly the same entities. This is deliberately strict: a
paraphrase that words an entity differently ("Christmas Eve" vs "December 24" is handled, "a dozen" is not)
loses a hit, which is cheaper than serving a wrong answer.
"""
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from scache.config import settings

NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60, "ninety": 90}
NUM = r"(?:\d+(?:\.\d+)?|" + "|".join(NUMWORDS) + r")"
DIGITS = r"\d[\d,]*(?:\.\d+)?"
UNIT = r"(business[\s-]days?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|yrs?)"
MINUTES = {"minute": 1, "hour": 60, "day": 1440, "week": 10080}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MON_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
            "oct": 10, "nov": 11, "dec": 12}
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)} | MON_ABBR
MON = "|".join(sorted(MONTH_NUM, key=len, reverse=True))
HOLIDAYS = [  # order matters: longer phrases first
    (r"(?:the\s+)?day\s+after\s+thanksgiving|black\s+friday", "hol:blackfriday"),
    (r"thanksgiving(?:\s+day)?", "hol:thanksgiving"),
    (r"christmas\s+eve", "date:12-24"),
    (r"christmas(?:\s+day)?", "date:12-25"),
    (r"new\s+year'?s?\s+eve", "date:12-31"),
    (r"new\s+year'?s?(?:\s+day)?", "date:01-01"),
]
POLICY_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([A-Z]{2}-\d+)\s*\|", re.M)


def _num(s: str) -> Decimal:
    return Decimal(NUMWORDS[s.lower()]) if s.lower() in NUMWORDS else Decimal(s.replace(",", ""))


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def load_products(policy_text: str) -> dict[str, str]:
    """Product name -> SKU from the policy's price table."""
    return {m[1]: m[2] for m in POLICY_ROW.finditer(policy_text) if m[1] != "Product"}


@lru_cache(maxsize=4)
def _products_from(path: str) -> dict[str, str]:
    return load_products(Path(path).read_text())


def default_products() -> dict[str, str]:
    return _products_from(str(settings.policy_path))


def _duration(a: str, b: str | None, unit: str) -> str:
    u = unit.lower()
    key = "bday" if u.startswith("business") else "minute" if u.startswith("min") else \
        "hour" if u.startswith(("hour", "hr")) else "day" if u.startswith("day") else \
        "week" if u.startswith("week") else "month" if u.startswith("month") else "year"
    f = MINUTES.get(key, 1)
    nums = [_fmt(_num(x) * f) for x in ([a] if b is None else [a, b])]
    return "dur:" + "-".join(nums) + ("min" if key in MINUTES else key)


def extract_entities(text: str, products: dict[str, str] | None = None) -> set[str]:
    products = default_products() if products is None else products
    sku_to_name = {sku: name for name, sku in products.items()}
    out: set[str] = set()
    s = text

    def take(pattern: str, make, flags: int = re.I) -> None:
        nonlocal s

        def repl(m):
            e = make(m)
            if e:
                out.add(e)
            return " "

        s = re.sub(pattern, repl, s, flags=flags)

    # identifiers: promo codes (any case after "code"/"coupon"), then upper-case tokens such as TP-40, FO-123456
    take(r"\b(?:code|coupon)s?\s+([A-Za-z]{3,}\d+)\b", lambda m: _id(m[1], sku_to_name))
    take(r"\b[A-Z]{2,}-?\d+\b", lambda m: _id(m[0], sku_to_name), flags=0)
    for name in sorted(products, key=len, reverse=True):
        take(r"\b" + r"\s+".join(map(re.escape, name.split())) + r"\b", lambda m, n=name: f"prod:{n.lower()}")
    take(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", lambda m: "tel:" + re.sub(r"\D", "", m[0]))
    take(rf"\$\s?({DIGITS})", lambda m: f"usd:{_fmt(_num(m[1]))}")
    take(rf"\b({DIGITS}|{NUM})\s*(?:dollars?|usd|bucks)\b", lambda m: f"usd:{_fmt(_num(m[1]))}")
    take(rf"\b({DIGITS})\s*(?:%|percent|per\s*cent)", lambda m: f"pct:{_fmt(_num(m[1]))}")
    take(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?", lambda m: _clock(m[1], m[2], m[3]))
    take(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lambda m: f"time:{int(m[1]):02d}:{m[2]}")
    take(r"\bnoon\b", lambda m: "time:12:00")
    take(r"\bmidnight\b", lambda m: "time:00:00")
    for pattern, ent in HOLIDAYS:
        take(r"\b(?:" + pattern + r")\b", lambda m, e=ent: e)
    take(rf"\b({MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lambda m: _date(m[1], m[2]))
    take(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MON})\b", lambda m: _date(m[2], m[1]))
    take(r"\b(" + "|".join(m for m in MONTHS if m != "may") + r")\b", lambda m: f"month:{MONTH_NUM[m[1].lower()]}")
    take(rf"\b({NUM})(?:\s*(?:to|-|–)\s*({NUM}))?[\s-]*{UNIT}\b", lambda m: _duration(m[1], m[2], m[3]))
    take(r"\b(mon|tues|wednes|thurs|fri|satur|sun)days?\b", lambda m: f"day:{m[1].lower()[:3]}")
    # abbreviated weekdays only when capitalised, so "I sat" or "sun damage" stay plain words
    take(r"\b(Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)\b\.?", lambda m: f"day:{m[1].lower()[:3]}", flags=0)
    take(r"\bweekdays?\b", lambda m: "range:weekday")
    take(r"\bweekends?\b", lambda m: "range:weekend")
    take(r"\b(\d+)(?!(?:st|nd|rd|th)\b)([a-z])\b", lambda m: f"model:{m[1]}{m[2].lower()}")
    take(rf"(?<![\w.])({DIGITS})(?![\w])", lambda m: f"num:{_fmt(_num(m[1]))}")
    # a count word on its own ("two promo codes"); "one" is left out because it is mostly a pronoun ("which one")
    take(r"\b(" + "|".join(w for w in NUMWORDS if w != "one") + r")\b", lambda m: f"num:{NUMWORDS[m[1].lower()]}")
    return out


def _id(token: str, sku_to_name: dict[str, str]) -> str:
    t = token.upper()
    return f"prod:{sku_to_name[t].lower()}" if t in sku_to_name else f"id:{t}"


def _clock(hour: str, minute: str | None, ap: str) -> str:
    h = int(hour) % 12 + (12 if ap.lower() == "p" else 0)
    return f"time:{h:02d}:{minute or '00'}"


def _date(month: str, day: str) -> str | None:
    d = int(day)
    return f"date:{MONTH_NUM[month.lower()]:02d}-{d:02d}" if 1 <= d <= 31 else None


def entities_compatible(q_new: str, q_cached: str, products: dict[str, str] | None = None) -> bool:
    """True when both questions mention exactly the same entities (including both mentioning none)."""
    return extract_entities(q_new, products) == extract_entities(q_cached, products)


def entity_diff(q_new: str, q_cached: str, products: dict[str, str] | None = None) -> tuple[set[str], set[str]]:
    """(only in the new question, only in the cached question). Both empty means compatible."""
    a, b = extract_entities(q_new, products), extract_entities(q_cached, products)
    return a - b, b - a
