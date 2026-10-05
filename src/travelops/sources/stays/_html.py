"""Small HTML tree and currency reader for search-page adapters."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

from ...core.money import Money
from ..base import ParseError


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return " ".join(child.text() if isinstance(child, Node) else child for child in self.children).strip()

    def find(self, **attrs):
        return next(
            (n for n in self.walk() if all(n.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())), None
        )


class Tree(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def money(text):
    symbols = {"€": "EUR", "$": "USD", "£": "GBP", "EUR": "EUR", "USD": "USD", "GBP": "GBP"}
    matches = re.findall(r"(EUR|USD|GBP|€|\$|£)\s*([\d][\d.,\s\u00a0]*)", text)
    if not matches:
        reverse = re.findall(r"([\d][\d.,\s\u00a0]*)\s*(EUR|USD|GBP|€|\$|£)", text)
        matches = [(code, value) for value, code in reverse]
    if not matches:
        raise ParseError(f"price has no supported currency: {text[:100]}")
    code, number = matches[-1]
    number = re.sub(r"\s+", "", number)
    if "," in number and "." in number:
        decimal_mark = "," if number.rfind(",") > number.rfind(".") else "."
        number = number.replace("." if decimal_mark == "," else ",", "").replace(decimal_mark, ".")
    elif "," in number:
        number = number.replace(",", "." if len(number.rsplit(",", 1)[1]) == 2 else "")
    elif number.count(".") > 1 or ("." in number and len(number.rsplit(".", 1)[1]) == 3):
        number = number.replace(".", "")
    try:
        return Money(number, symbols[code])
    except Exception as exc:
        raise ParseError("invalid quoted money amount") from exc
