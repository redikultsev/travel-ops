"""Check what an agent did with a recorded search. The checks are mechanical on purpose: a price that no tool
returned, a link that no tool returned, a search where a view would do. They need no model and give the same
verdict every time.

A run is a directory with, for every turn N of the scenario, `turn-N.md` (the agent's answer) and
`turn-N.trace.jsonl` (written by the server when TRAVELOPS_TRACE points there)."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path

from .kinds import KINDS
from .replay import load_scenario

SEARCHES = ("search_trip", *(kind.tool for kind in KINDS.values()))
CURRENCIES = {
    "EUR": "EUR",
    "€": "EUR",
    "ЕВРО": "EUR",
    "EURO": "EUR",
    "RUB": "RUB",
    "₽": "RUB",
    "РУБ": "RUB",
    "Р": "RUB",
    "USD": "USD",
    "$": "USD",
    "RSD": "RSD",
}
# Thousands in threes with a space or a separator, then at most two decimals: "21:25. 10 701 RUB" is 10 701.
_NUMBER = r"(?<![\d.,:])(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?(?!\d)"
_CODE = r"EUR|€|евро|euro|RUB|₽|руб(?:лей|ля|\.)?|USD|\$|RSD"
MONEY = re.compile(rf"(?:(?P<pre>{_CODE})\s?(?P<a>{_NUMBER}))|(?:(?P<b>{_NUMBER})\s?(?P<post>{_CODE}))", re.IGNORECASE)
URL = re.compile(r"https?://[^\s<>\"'`|]+")


def clean_url(url: str) -> str:
    """A link as written in prose or markdown: brackets of the link syntax and trailing punctuation are not part of
    it, brackets inside it (route[0]=...) are."""
    url = url.rstrip(".,;:!?")
    while url and url[-1] in ")]" and url.count(url[-1]) > url.count("(" if url[-1] == ")" else "["):
        url = url[:-1].rstrip(".,;:!?")
    return url


ALIASES = {
    "aviasales": ("aviasales", "авиасейлс"),
    "tutu": ("tutu", "туту"),
    "onetwotrip": ("onetwotrip", "one two trip"),
    "kupibilet": ("kupibilet", "купибилет"),
    "wildberries": ("wildberries", "wb travel", "вайлдберриз"),
    "booking": ("booking", "букинг"),
    "airbnb": ("airbnb", "эйрбиэнби"),
}


def number(text: str) -> Decimal | None:
    """`10 701`, `10,701`, `113.60` and `113,60` as people write them."""
    text = re.sub(r"[\s  ]", "", text)
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
        text = re.sub(r"[.,]", "", text)
    elif re.fullmatch(r"\d{1,3}(,\d{3})+\.\d+", text):
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def currency(token: str) -> str:
    token = token.upper().rstrip(".")
    return CURRENCIES.get(token) or ("RUB" if token.startswith("РУБ") else token)


def amounts_in(text: str) -> list[tuple[Decimal, str, str]]:
    found = []
    for match in MONEY.finditer(text):
        value = number(match["a"] or match["b"])
        if value is not None:
            found.append((value, currency(match["pre"] or match["post"]), match[0]))
    return found


def collect(value, amounts: set, links: set) -> None:
    """Every amount of money and every link anywhere in a tool answer."""
    if isinstance(value, dict):
        if set(value) == {"amount", "currency"}:
            amounts.add((Decimal(str(value["amount"])), value["currency"]))
        if isinstance(value.get("url"), str):
            links.add(value["url"])
        for item in value.values():
            collect(item, amounts, links)
    elif isinstance(value, list):
        for item in value:
            collect(item, amounts, links)
    elif isinstance(value, str) and value.startswith("http"):
        links.add(value)


def same(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str.lower, map(str, a))) == sorted(map(str.lower, map(str, b)))
    return a == b


def matches(call: dict, wanted: dict) -> bool:
    if call["tool"] != wanted["tool"]:
        return False
    for name, value in (wanted.get("arguments") or {}).items():
        options = value["any"] if isinstance(value, dict) and "any" in value else [value]
        if not any(same(call["arguments"].get(name), option) for option in options):
            return False
    return True


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def check_turn(turn: dict, answer: str, calls: list[dict], amounts: set, links: set) -> list[str]:
    """Failures of one turn. `amounts` and `links` hold everything the tools have returned so far."""
    failures = []
    rules = turn.get("calls") or {}
    for wanted in rules.get("require") or []:
        if not any(matches(call, wanted) for call in calls):
            failures.append(f"no call like {json.dumps(wanted, ensure_ascii=False)}")
    for name in rules.get("forbid") or []:
        if any(call["tool"] == name for call in calls):
            failures.append(f"called {name}, which this turn must not need")
    # A call answered with needs_confirmation asked the human first and searched nothing.
    searches = [
        call["tool"]
        for call in calls
        if call["tool"] in SEARCHES and not (call.get("result") or {}).get("needs_confirmation")
    ]
    if "searches_at_most" in rules and len(searches) > rules["searches_at_most"]:
        failures.append(
            f"{len(searches)} searches ({', '.join(searches)}), at most {rules['searches_at_most']} allowed"
        )
    for call in calls:
        if "error" in call and not rules.get("errors_allowed"):
            failures.append(f"{call['tool']} failed: {call['error']}")

    expect = turn.get("answer") or {}
    allowed = {Decimal(str(x)) for x in expect.get("allowed_amounts") or []}
    by_currency: dict[str, set[Decimal]] = {}
    for value, code in amounts:
        by_currency.setdefault(code, set()).add(value)
    for value, code, written in amounts_in(answer):
        known = by_currency.get(code, set())
        if value in allowed or any(abs(value - k) < Decimal("0.005") for k in known):
            continue
        # A total of two returned prices, or the difference between two, is arithmetic, not invention.
        if any(
            abs(value - (a + b)) < Decimal("0.011") or abs(value - abs(a - b)) < Decimal("0.011")
            for a, b in combinations(known, 2)
        ):
            continue
        failures.append(f"price {written!r} is in no tool result")
    written_links = {clean_url(url) for url in URL.findall(answer)}
    for url in sorted(written_links - links):
        failures.append(f"link is in no tool result: {url[:120]}")
    if len(written_links) < expect.get("links_at_least", 0):
        failures.append(f"{len(written_links)} links, at least {expect['links_at_least']} expected")
    if "chars_at_most" in expect and len(answer) > expect["chars_at_most"]:
        failures.append(f"the answer is {len(answer)} characters, at most {expect['chars_at_most']} expected")
    if expect.get("no_tables", True) and re.search(r"^\s*\|?\s*:?-{3,}:?\s*\|", answer, re.MULTILINE):
        failures.append("the answer has a table; many chat clients do not render them")
    if expect.get("language") == "ru" and len(re.findall(r"[а-яё]", answer, re.IGNORECASE)) < len(answer) * 0.2:
        failures.append("the answer is not in Russian")
    if expect.get("mention_all_sources"):
        sources = {r["source"] for call in calls for r in _reports(call.get("result"))}
        low = answer.lower()
        for source in sorted(sources):
            if not any(alias in low for alias in ALIASES.get(source, (source,))):
                failures.append(f"source {source} is not reported")
    for pattern in expect.get("must_match") or []:
        if not re.search(pattern, answer, re.IGNORECASE):
            failures.append(f"nothing in the answer matches /{pattern}/")
    for pattern in expect.get("must_not_match") or []:
        if found := re.search(pattern, answer, re.IGNORECASE):
            failures.append(f"the answer has {found[0]!r}, matching /{pattern}/")
    return failures


def _reports(result) -> list[dict]:
    if not isinstance(result, dict):
        return []
    found = list(result.get("sources") or [])
    for part in ("flights", "stays"):
        if isinstance(result.get(part), dict):
            found += result[part].get("sources") or []
    return [r for r in found if isinstance(r, dict) and "source" in r]


def check_run(scenario_dir: Path, run_dir: Path) -> list[dict]:
    scenario = load_scenario(Path(scenario_dir))
    amounts: set = set()
    links: set = set()
    report = []
    for index, turn in enumerate(scenario["turns"], 1):
        answer_path = Path(run_dir) / f"turn-{index}.md"
        calls = read_trace(Path(run_dir) / f"turn-{index}.trace.jsonl")
        for call in calls:
            collect(call.get("result"), amounts, links)
        if not answer_path.exists():
            failures = [f"no answer: {answer_path.name} is missing"]
        else:
            failures = check_turn(turn, answer_path.read_text(), calls, amounts, links)
        report.append({"turn": index, "say": turn["say"], "calls": [c["tool"] for c in calls], "failures": failures})
    return report
