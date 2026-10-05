"""Recorded answers go into the public repo as test fixtures: tokens, sessions and e-mails must not."""

from __future__ import annotations

import json
import re

SECRET_KEY = re.compile(r"(token|session|csrf|auth|cookie|secret|sign|api_?key|password)", re.I)
HTML_SECRET = re.compile(
    rb'(["\']?[\w-]*(?:token|session|csrf|auth|secret|api_?key)[\w-]*["\']?\s*[:=]\s*["\'])'
    rb'[^"\']+',
    re.I,
)
EMAIL = re.compile(rb"[\w.+-]+@[\w-]+\.[\w.-]+")


def _walk(node):
    if isinstance(node, dict):
        return {
            k: (
                "SCRUBBED"
                if (k in {"checkout_ref", "offer_hash"} or (SECRET_KEY.search(k) and isinstance(v, (str, int))))
                else _walk(v)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_walk(v) for v in node]
    return node


def scrub(body: bytes) -> bytes:
    try:
        out = json.dumps(_walk(json.loads(body)), ensure_ascii=False).encode()
    except ValueError:
        out = HTML_SECRET.sub(rb"\1SCRUBBED", body)
    return EMAIL.sub(b"user@example.com", out)
