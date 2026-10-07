"""Refresh the list of airports with scheduled passenger flights from OurAirports (public domain,
https://ourairports.com/data/). The airport table the tool uses does not say which airports airlines fly to:
without this list a place is served by a closed airport (Istanbul Atatürk) or an air base (Yokota, Tokyo).

uv run python scripts/update_airports.py [path/to/airports.csv]
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCE = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = Path(__file__).resolve().parents[1] / "src" / "travelops" / "data" / "scheduled_airports.txt"
KINDS = {"large_airport", "medium_airport", "small_airport"}


def main(path: str | None = None) -> int:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(SOURCE, timeout=120) as response:
            text = response.read().decode("utf-8")
    codes = sorted(
        {
            row["iata_code"].strip().upper()
            for row in csv.DictReader(io.StringIO(text))
            if row["scheduled_service"] == "yes" and row["type"] in KINDS and len(row["iata_code"].strip()) == 3
        }
    )
    header = (
        f"# Airports with scheduled passenger flights, by IATA code. OurAirports (public domain), {date.today()}.\n"
    )
    OUT.write_text(header + "\n".join(codes) + "\n")
    print(f"{len(codes)} airports written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
