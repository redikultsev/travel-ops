import json
from datetime import date
import pytest
from travelops import cli
from travelops.app import build
from travelops.kinds import KINDS
from travelops.search import FlightSearch
from tests.fakes import fake_searches


def test_arguments_and_sources_validation():
    args = cli.parser().parse_args(["flights", "BEG", "MOW", "2026-11-14", "--sources", "tutu,onetwotrip"])
    assert args.depart == date(2026, 11, 14) and args.sources == ["tutu", "onetwotrip"]
    with pytest.raises(ValueError, match="known sources"):
        KINDS["flights"].sources(["missing"])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["flights", "BEG", "MOW", "bad-date"])


async def test_json_yes_and_profile_defaults(tmp_path, monkeypatch, capsys):
    (tmp_path / "profile.yml").write_text("travellers: {adults: 2}\ncabin: business\n")
    app = build(tmp_path, None)
    monkeypatch.setattr(cli, "build", lambda *args: app)

    async def search(query, sources, ctx, rates, currency, **kwargs):
        assert query.adults == 2 and query.cabin == "business"
        return FlightSearch(query, currency, [], [])

    monkeypatch.setattr(cli, "Searches", lambda app: fake_searches(app, flights=search, estimate=lambda *a: 400))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("must not prompt with --yes"))
    args = cli.parser().parse_args(["flights", "BEG", "MOW", "2026-11-14", "--json", "--yes"])
    assert await cli.execute(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"]["adults"] == 2 and payload["sources"] == []
    assert payload["search_id"].startswith("f"), "kept in memory, so an agent can refine it"


async def test_sources_reset_calls_limiter(tmp_path, monkeypatch, capsys):
    app = build(tmp_path, None)
    app.limiter.blocked("tutu")
    monkeypatch.setattr(cli, "build", lambda *args: app)
    args = cli.parser().parse_args(["sources", "--reset", "tutu"])
    assert await cli.execute(args) == 0
    assert "Reset tutu" in capsys.readouterr().out


async def test_invalid_return_rejected_before_search(tmp_path, monkeypatch):
    app = build(tmp_path, None)
    monkeypatch.setattr(cli, "build", lambda *args: app)
    args = cli.parser().parse_args(["flights", "BEG", "MOW", "2026-11-14", "--return", "2026-11-01"])
    with pytest.raises(ValueError, match="return"):
        await cli.execute(args)


async def test_declining_long_search_does_not_load_rates_or_search(tmp_path, monkeypatch):
    app = build(tmp_path, None)
    monkeypatch.setattr(cli, "build", lambda *args: app)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "no")

    async def unexpected(*args, **kwargs):
        pytest.fail("declined search must not run")

    monkeypatch.setattr(
        cli, "Searches", lambda app: fake_searches(app, flights=unexpected, load=unexpected, estimate=lambda *a: 400)
    )
    args = cli.parser().parse_args(["flights", "BEG", "MOW", "2026-11-14"])
    assert await cli.execute(args) == 0 and app.closed


def test_mcp_over_http_is_asked_for():
    assert cli.parser().parse_args(["mcp"]).http is False, "standard input and output stay the default"
    args = cli.parser().parse_args(["mcp", "--http", "--host", "0.0.0.0"])
    assert (args.http, args.host, args.port) == (True, "0.0.0.0", 8765)
