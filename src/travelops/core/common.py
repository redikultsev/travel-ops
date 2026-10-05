from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LinkKind = Literal["ticket", "results", "property"]


@dataclass(frozen=True)
class Link:
    """Where the price can be bought. `ticket`: the exact offer; `results`: a results page that should contain it;
    `property`: a stay page with the dates filled in."""

    url: str
    kind: LinkKind
