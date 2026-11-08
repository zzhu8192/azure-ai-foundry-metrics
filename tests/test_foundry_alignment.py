"""Regression checks for comparing the CLI with Foundry's Monitor view."""

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from rich.console import Console

from foundry_metrics.aggregator import MetricsAggregator
from foundry_metrics.cli import main
from foundry_metrics.cost_management import BilledUsageRow, match_meter_to_model
from foundry_metrics.models import CostBreakdown, DeploymentInfo, ResourceInfo
from foundry_metrics.monitor import COST_METRIC, MonitorMetricsClient, ResourceMetrics
from foundry_metrics.pricing import PricingManager
from foundry_metrics.time_windows import calculate_windows
from foundry_metrics.views import render_dashboard


@pytest.fixture
def resource():
    return ResourceInfo(
        id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acc",
        name="acc", resource_group="rg", subscription_id="sub", kind="AIServices",
        location="southcentralus",
        deployments=[DeploymentInfo("gpt-6.1-sol", "gpt-6.1-sol")],
    )


def metric(name, points, deployment="gpt-6.1-sol", dimension="ModelDeploymentName"):
    return {"name": {"value": name}, "timeseries": [{
        "metadatavalues": [{"name": {"value": dimension}, "value": deployment}],
        "data": points,
    }]}


def screenshot_metrics(resource):
    client = MonitorMetricsClient(MagicMock())
    raw = {"value": [
        metric("InputTokens", [{"total": 17_380_000}]),
        metric("OutputTokens", [{"total": 174_430}]),
        metric(COST_METRIC, [{"total": 2.00}, {"total": 2.93}]),
    ]}
    return ResourceMetrics(client._parse_metrics_response(raw, resource), client._parse_estimated_costs(raw, resource))


def test_sol_rates_and_version_boundaries():
    pricing = PricingManager()
    name, rate = pricing.resolve_rate("openai-gpt-6.1-sol-2026-10-20")
    assert name == "gpt-6.1-sol"
    assert (rate.input_uncached, rate.input_cached, rate.cache_write, rate.output) == (2, .1, 2.5, 10)
    with pytest.warns(UserWarning, match="No rate card"):
        name, _ = pricing.resolve_rate("gpt-6.2-sol")
    assert name == "gpt-6.2-sol"
    # A newer unknown model must not inherit an older model's rate or meter.
    assert match_meter_to_model("6.1 sol ShortCo Inp Std Gl 1M Tokens", ["gpt-6", "gpt-6.1-sol"]) == "gpt-6.1-sol"
    assert match_meter_to_model("6.2 sol ShortCo Inp Std Gl 1M Tokens", ["gpt-6"]) is None


def test_portal_estimate_survives_delayed_billing(resource):
    aggregator = MetricsAggregator(MagicMock())
    aggregator.monitor_client = MagicMock()
    aggregator.monitor_client.query_resource_metrics.return_value = screenshot_metrics(resource)
    window = calculate_windows("utc", reference_dt=datetime(2026, 11, 7, 12, tzinfo=timezone.utc))["7d"]
    # Delayed billing reports only a small fraction of live usage. It must not
    # overwrite live tokens, the Foundry estimate, or the rate-card estimate.
    rows = {"sub": [BilledUsageRow(resource.id.lower(), "6.1 sol ShortCo Inp Std Gl 1M Tokens", 20261106, .25, .125)]}
    period = aggregator._collect_single_period(window, [resource], rows)
    model = next(iter(period.models.values()))
    assert model.tokens.total_input == 17_380_000
    assert model.tokens.output == 174_430
    assert model.cost.estimated_total_cost == pytest.approx(4.93)
    assert model.cost.rate_card_total_cost == pytest.approx(36.5043)
    assert model.cost.billed_cost == .25
    assert period.total_cost.foundry_estimated_cost == pytest.approx(4.93)
    assert period.total_cost.estimate_source == "foundry_metric"

    stream = io.StringIO()
    render_dashboard([period], Console(file=stream, width=200), "json")
    report = json.loads(stream.getvalue())[0]
    assert report["total_cost"]["estimated_usd"] == 4.93
    assert report["total_cost"]["billed_usd"] == .25
    assert report["total_cost"]["effective_usd"] == 4.93
    assert report["total_cost"]["source"] == "foundry_metric"

    stream = io.StringIO()
    render_dashboard([period], Console(file=stream, width=40), "csv")
    csv_rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
    assert csv_rows[0]["CostSource"] == "foundry_metric"
    assert csv_rows[0]["FoundryEstimatedCostUSD"] == "4.9300"
    assert csv_rows[0]["RateCardEstimatedCostUSD"] == "36.5043"
    assert all(None not in row and None not in row.values() for row in csv_rows)


def test_missing_and_zero_portal_cost_are_distinct(resource):
    client = MonitorMetricsClient(MagicMock())
    assert client._parse_estimated_costs({"value": [metric(COST_METRIC, [{"total": None}])]}, resource) == {}
    assert client._parse_estimated_costs({"value": [metric(COST_METRIC, [{"total": 0}])]}, resource) == {"gpt-6.1-sol": 0}
    cost = CostBreakdown(uncached_input_cost=100, foundry_estimated_cost=0, estimate_source="foundry_metric")
    assert cost.estimated_total_cost == 0


def test_mixed_estimates_and_deployment_filter(resource):
    resource.deployments.append(DeploymentInfo("other", "gpt-4o"))
    aggregator = MetricsAggregator(MagicMock(), enable_cost_management=False)
    aggregator.monitor_client = MagicMock()
    metrics = screenshot_metrics(resource)
    metrics.tokens["other"] = metrics.tokens["gpt-6.1-sol"]
    aggregator.monitor_client.query_resource_metrics.return_value = metrics
    window = calculate_windows("utc")["today"]
    mixed = aggregator._collect_single_period(window, [resource])
    assert mixed.total_cost.estimate_source == "mixed"
    assert mixed.total_cost.estimated_total_cost > 4.93
    assert mixed.total_cost.foundry_estimated_cost is None
    selected = aggregator._collect_single_period(window, [resource], target_deployment="gpt-6.1-sol")
    assert len(selected.models) == 1
    assert selected.total_cost.estimated_total_cost == pytest.approx(4.93)
    assert selected.total_cost.estimate_source == "foundry_metric"


def test_shared_model_billing_is_not_duplicated(resource):
    resource.deployments.append(DeploymentInfo("sol-second", "gpt-6.1-sol"))
    aggregator = MetricsAggregator(MagicMock())
    aggregator.monitor_client = MagicMock()
    metrics = screenshot_metrics(resource)
    metrics.tokens["sol-second"] = metrics.tokens["gpt-6.1-sol"]
    metrics.estimated_costs["sol-second"] = 1
    aggregator.monitor_client.query_resource_metrics.return_value = metrics
    window = calculate_windows("utc")["today"]
    rows = {"sub": [BilledUsageRow(resource.id, "6.1 sol ShortCo Inp Std Gl 1M Tokens", int(window.start_utc.strftime("%Y%m%d")), 10, 1)]}
    period = aggregator._collect_single_period(window, [resource], rows)
    assert all(m.cost.billed_cost is None for m in period.models.values())
    assert period.unattributed_billed_cost == 10
    assert period.total_cost.billed_cost == 10
    assert period.total_cost.estimated_total_cost == pytest.approx(5.93)


def test_queries_discover_metrics_and_separate_aggregations(resource, monkeypatch):
    calls = []
    definitions = [{
        "name": {"value": name}, "dimensions": [{"value": "ModelDeploymentName"}],
        "supportedAggregationTypes": [aggregation],
    } for name, aggregation in [("InputTokens", "Total"), (COST_METRIC, "Total"), ("AzureOpenAIContextTokensCacheMatchRate", "Average")]]

    def get(url, headers, params, timeout):
        calls.append((url, dict(params)))
        response = MagicMock(ok=True, status_code=200)
        if url.endswith("metricDefinitions"):
            response.json.return_value = {"value": definitions}
        else:
            values = []
            for name in params["metricnames"].split(","):
                aggregation = "average" if name == "AzureOpenAIContextTokensCacheMatchRate" else "total"
                values.append(metric(name, [{aggregation: 50 if aggregation == "average" else 100}]))
            response.json.return_value = {"value": values}
        return response

    monkeypatch.setattr("foundry_metrics.monitor.requests.get", get)
    client = MonitorMetricsClient(MagicMock())
    window = calculate_windows("utc")["today"]
    result = client.query_resource_metrics(resource, window)
    assert result.tokens["gpt-6.1-sol"].cached_input == 50
    assert result.estimated_costs["gpt-6.1-sol"] == 100
    queries = [p for url, p in calls if not url.endswith("metricDefinitions")]
    assert len(queries) == 2
    assert {p["aggregation"] for p in queries} == {"Total", "Average"}
    assert all(p["$filter"] == "ModelDeploymentName eq '*'" for p in queries)
    assert all("GeneratedTokens" not in p["metricnames"] for p in queries)
    client.query_resource_metrics(resource, window)
    assert sum(url.endswith("metricDefinitions") for url, _ in calls) == 1


def test_multiple_series_and_aliases_do_not_lose_or_duplicate_tokens(resource):
    client = MonitorMetricsClient(MagicMock())
    first = metric("InputTokens", [{"total": 100}])
    first["timeseries"].extend(metric("InputTokens", [{"total": 200}])["timeseries"])
    raw = {"value": [first, metric("ProcessedPromptTokens", [{"total": 300}]),
                     metric(COST_METRIC, [{"total": 1}]), metric(COST_METRIC, [{"total": 2}])]}
    assert client._parse_metrics_response(raw, resource)["gpt-6.1-sol"].total_input == 300
    assert client._parse_estimated_costs(raw, resource)["gpt-6.1-sol"] == 3
    model_only = {"value": [metric("InputTokens", [{"total": 10}], dimension="ModelName")]}
    assert set(client._parse_metrics_response(model_only, resource)) == {"gpt-6.1-sol"}


def test_missing_metric_does_not_discard_live_tokens(resource, monkeypatch):
    def get(url, headers, params, timeout):
        response = MagicMock(ok=False, status_code=400)
        if url.endswith("metricDefinitions"):
            response.status_code = 403
        elif params["metricnames"] == "InputTokens":
            response.ok, response.status_code = True, 200
            response.json.return_value = {"value": [metric("InputTokens", [{"total": 1234}])]}
        return response

    monkeypatch.setattr("foundry_metrics.monitor.requests.get", get)
    result = MonitorMetricsClient(MagicMock()).query_resource_metrics(resource, calculate_windows("utc")["today"])
    assert result.tokens["gpt-6.1-sol"].total_input == 1234
    assert not result.estimated_costs
    assert result.notes


def test_chicago_windows_across_dst():
    reference = datetime(2026, 11, 7, 18, tzinfo=timezone.utc)
    windows = calculate_windows("America/Chicago", reference_dt=reference)
    assert windows["month"].start_utc == datetime(2026, 11, 1, 5, tzinfo=timezone.utc)
    assert windows["week"].start_utc == datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
    assert windows["7d"].end_utc - windows["7d"].start_utc == timedelta(hours=168)
    assert windows["7d"].start.hour == 13
    custom = calculate_windows("America/Chicago", start="2026-10-31", end="2026-11-08")["custom"]
    assert custom.start_utc == datetime(2026, 10, 31, 5, tzinfo=timezone.utc)
    assert custom.end_utc == datetime(2026, 11, 8, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize("args, expected", [
    (["--period", "7d"], "Last 7 Days"),
    (["--start", "2026-10-31", "--end", "2026-11-08"], "Custom"),
])
def test_cli_comparison_ranges(args, expected):
    result = CliRunner().invoke(main, ["--demo", "--timezone", "America/Chicago", "--format", "json", *args])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1 and data[0]["period"] == expected
    assert data[0]["timezone"] == "America/Chicago"


@pytest.mark.parametrize("args", [
    ["--end", "2026-11-08"], ["--start", "2026-11-08", "--end", "2026-11-01"],
    ["--start", "invalid"], ["--timezone", "Invalid/Zone"],
])
def test_invalid_range_rejected_before_auth(args):
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 2
    assert "Azure Authentication Required" not in result.output


def test_reported_total_preserves_discrepancy_without_inventing_input(resource):
    # Synthetic component split illustrating the user's 26.62M vs 23.23M gap.
    client = MonitorMetricsClient(MagicMock())
    raw = {"value": [metric("InputTokens", [{"total": 23_000_000}]),
                     metric("OutputTokens", [{"total": 230_000}]),
                     metric("TotalTokens", [{"total": 26_620_000}])]}
    tokens = client._parse_metrics_response(raw, resource)["gpt-6.1-sol"]
    assert tokens.total_tokens == 26_620_000
    assert tokens.component_total_tokens == 23_230_000
    assert tokens.total_difference == 3_390_000
    assert tokens.uncached_input == 23_000_000
    assert tokens.output == 230_000
    assert tokens.total_source == "TotalTokens"

    aggregator = MetricsAggregator(MagicMock(), enable_cost_management=False)
    aggregator.monitor_client = MagicMock()
    aggregator.monitor_client.query_resource_metrics.return_value = ResourceMetrics(
        tokens={"gpt-6.1-sol": tokens}, metric_totals={"gpt-6.1-sol": {"TotalTokens": 26_620_000}},
    )
    period = aggregator._collect_single_period(calculate_windows("utc")["today"], [resource])
    assert period.total_tokens.total_tokens == 26_620_000
    assert period.total_tokens.total_source == "TotalTokens"
    # Rate-card cost uses actual input/output counts, not a guessed attribution.
    assert period.total_cost.estimated_total_cost == pytest.approx(48.3)
    assert any("input + output reports 23,230,000" in note for note in period.estimate_notes)
    stream = io.StringIO()
    render_dashboard([period], Console(file=stream), "json")
    report = json.loads(stream.getvalue())[0]
    assert report["total_tokens"]["reported_total_difference"] == 3_390_000
    assert next(iter(report["models"].values()))["metric_totals"]["TotalTokens"] == 26_620_000


@pytest.mark.parametrize("sample, expected_source, expected_total", [
    (None, "input_plus_output", 100), (0, "TotalTokens", 0), (50, "TotalTokens", 50),
])
def test_missing_zero_and_lower_token_totals(resource, sample, expected_source, expected_total):
    raw = {"value": [metric("InputTokens", [{"total": 100}]), metric("TotalTokens", [{"total": sample}])]}
    tokens = MonitorMetricsClient(MagicMock())._parse_metrics_response(raw, resource)["gpt-6.1-sol"]
    assert tokens.total_tokens == expected_total
    assert tokens.total_source == expected_source
    assert tokens.total_input == 100


def test_mixed_token_total_sources():
    from foundry_metrics.models import TokenBreakdown
    portal = TokenBreakdown(uncached_input=100, reported_total=120, total_source="TotalTokens")
    legacy = TokenBreakdown(uncached_input=200, output=10)
    total = TokenBreakdown().add(portal).add(legacy)
    assert total.total_tokens == 330
    assert total.component_total_tokens == 310
    assert total.total_difference == 20
    assert total.total_source == "mixed"
