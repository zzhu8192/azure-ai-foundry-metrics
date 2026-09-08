"""Integration tests for CLI invocation and flags."""

import json
from click.testing import CliRunner
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


def test_cli_demo_csv():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--period", "today", "--format", "csv"])
    assert result.exit_code == 0
    assert "Period,Resource,Deployment,Model" in result.output
    assert "Today" in result.output
    assert "gpt-4o" in result.output


def test_cli_demo_summary():
    runner = CliRunner()
    result = runner.invoke(main, ["--demo", "--format", "summary"])
    assert result.exit_code == 0
    assert "SUMMARY COMPARISON" in result.output
    assert "Today" in result.output
    assert "Current Week" in result.output
    assert "Current Month" in result.output
