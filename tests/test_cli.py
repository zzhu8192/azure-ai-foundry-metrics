"""Integration tests for CLI invocation and flags."""

import csv
import io
import json
from click.testing import CliRunner
from rich.console import Console
from foundry_metrics.cli import main


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Azure AI Foundry Metrics CLI" in result.output
    assert "--period" in result.output
    assert "--demo" in result.output


def test_cli_rates():
    runner = CliRunner()
    result = runner.invoke(main, ["rates"])
    assert result.exit_code == 0
    assert "gpt-4o" in result.output
    assert "claude-3-5-sonnet" in result.output


def test_cli_demo_table():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--period", "today"])
    assert result.exit_code == 0
    assert "TODAY" in result.output
    assert "gpt-4o" in result.output
    assert "Cached" in result.output
    assert "Est. Cost" in result.output


def test_cli_demo_json():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--period", "today", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["period"] == "Today"
    assert "total_tokens" in data[0]
    assert data[0]["total_tokens"]["uncached_input"] > 0
    assert data[0]["total_tokens"]["cached_input"] > 0


def test_cli_demo_csv(monkeypatch):
    monkeypatch.setattr("foundry_metrics.cli.console", Console(width=40))
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--format", "csv"])
    assert result.exit_code == 0
    reader = csv.DictReader(io.StringIO(result.output))
    rows = list(reader)
    assert len(reader.fieldnames) == 16
    assert {row["Period"] for row in rows} == {
        "Today", "Today - TOTAL",
        "Current Week", "Current Week - TOTAL",
        "Current Month", "Current Month - TOTAL",
    }
    assert any(row["Model"] == "gpt-4o" for row in rows)
    for row in rows:
        assert None not in row and None not in row.values()
        assert int(row["UncachedInputTokens"]) > 0
        assert int(row["TotalTokens"]) > 0
        assert float(row["EstimatedCostUSD"]) > 0


def test_cli_demo_summary():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--format", "summary"])
    assert result.exit_code == 0
    assert "SUMMARY COMPARISON" in result.output
    assert "Today" in result.output
    assert "Current Week" in result.output
    assert "Current Month" in result.output


def test_cli_demo_daily_default_7d():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--daily", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 7
    # Verify each element has a date period
    for item in data:
        assert len(item["period"]) == 10  # YYYY-MM-DD
        assert "total_tokens" in item


def test_cli_demo_daily_3d():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--period", "3d", "--daily", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 3


def test_cli_demo_3d_rolling():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--period", "3d", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["period"] == "Last 3 Days"
