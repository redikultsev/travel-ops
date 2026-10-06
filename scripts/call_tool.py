"""Call one tool of the travel-ops MCP server over stdio and print its JSON. For checking what an agent sees.

uv run python scripts/call_tool.py airports_near '{"place": "Kotor", "country": "Montenegro"}'
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


async def main(tool: str, arguments: dict) -> int:
    # The whole environment goes through: TRAVELOPS_DATA, TRAVELOPS_REPLAY and TRAVELOPS_TRACE are read by the server.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from travelops.cli import main; main(['mcp'])"],
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    async with Client(params, read_timeout_seconds=900) as client:
        if tool == "--list":
            listed = await client.list_tools()
            print(
                json.dumps(
                    [
                        {
                            "name": t.name,
                            "description": t.description,
                            "arguments": t.input_schema["properties"],
                            "required": t.input_schema.get("required", []),
                        }
                        for t in listed.tools
                    ],
                    ensure_ascii=False,
                    indent=1,
                )
            )
            return 0
        result = await client.call_tool(tool, arguments)
        if result.is_error:
            print(json.dumps({"error": result.content[0].text}, ensure_ascii=False))
            return 1
        print(json.dumps(result.structured_content, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    raise SystemExit(asyncio.run(main(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) == 3 else {})))
