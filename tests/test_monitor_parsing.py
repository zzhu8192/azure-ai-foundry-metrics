"""Unit tests for Azure Monitor API response parsing."""

from unittest.mock import MagicMock
from foundry_metrics.models import ResourceInfo, DeploymentInfo
from foundry_metrics.monitor import MonitorMetricsClient


def test_parse_monitor_metrics():
    mock_cred = MagicMock()
    client = MonitorMetricsClient(mock_cred)

    res = ResourceInfo(
        id="/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
        name="acc1",
        resource_group="rg1",
        subscription_id="sub1",
        kind="OpenAI",
        location="eastus2",
        deployments=[DeploymentInfo(name="gpt-4o-prod", model_name="gpt-4o")],
    )

    mock_resp = {
        "value": [
            {
                "name": {"value": "ProcessedPromptTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-4o-prod"}
                        ],
                        "data": [{"total": 10000.0}],
                    }
                ],
            },
            {
                "name": {"value": "AzureOpenAIContextTokensCacheMatchRate"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-4o-prod"}
                        ],
                        "data": [{"average": 50.0}],
                    }
                ],
            },
            {
                "name": {"value": "GeneratedTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-4o-prod"}
                        ],
                        "data": [{"total": 2500.0}],
                    }
                ],
            },
        ]
    }

    tokens_by_dep = client._parse_metrics_response(mock_resp, res)
    assert "gpt-4o-prod" in tokens_by_dep
    tb = tokens_by_dep["gpt-4o-prod"]

    # Total prompt = 10,000, 50% match rate -> cached = 5,000, uncached = 5,000
    assert tb.cached_input == 5000
    assert tb.uncached_input == 5000
    assert tb.total_input == 10000
    assert tb.output == 2500
    assert tb.total_tokens == 12500
    assert tb.cache_hit_rate == 50.0


def test_parse_monitor_metrics_alias_deduplication():
    """Verify that when Azure Monitor returns both ProcessedPromptTokens & InputTokens,

    or GeneratedTokens & OutputTokens, they are deduplicated instead of being summed together.
    """
    mock_cred = MagicMock()
    client = MonitorMetricsClient(mock_cred)

    res = ResourceInfo(
        id="/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
        name="acc1",
        resource_group="rg1",
        subscription_id="sub1",
        kind="OpenAI",
        location="southcentralus",
        deployments=[DeploymentInfo(name="gpt-5.6-terra", model_name="gpt-5.6-terra")],
    )

    # Both ProcessedPromptTokens AND InputTokens present with same 20M count
    # Both GeneratedTokens AND OutputTokens present with same 133.5K count
    # Also ephemeral1hInputTokens present for cache writes
    mock_resp = {
        "value": [
            {
                "name": {"value": "ProcessedPromptTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"total": 20000000.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
            {
                "name": {"value": "InputTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"total": 20000000.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
            {
                "name": {"value": "GeneratedTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"total": 133500.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
            {
                "name": {"value": "OutputTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"total": 133500.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
            {
                "name": {"value": "AzureOpenAIContextTokensCacheMatchRate"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"average": 96.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
            {
                "name": {"value": "ephemeral1hInputTokens"},
                "timeseries": [
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": "gpt-5.6-terra"}
                        ],
                        "data": [{"total": 600000.0, "timeStamp": "2026-09-01T00:00:00Z"}],
                    }
                ],
            },
        ]
    }

    tokens_by_dep = client._parse_metrics_response(mock_resp, res)
    assert "gpt-5.6-terra" in tokens_by_dep
    tb = tokens_by_dep["gpt-5.6-terra"]

    # Deduplicated prompt tokens: 20M, NOT 40M
    # Cache hit rate 96%: 20M * 96% = 19.2M
    # Cache write tokens: 600K
    # Uncached: 20M - 19.2M - 600K = 200K
    assert tb.cached_input == 19200000
    assert tb.cache_write == 600000
    assert tb.uncached_input == 200000
    # Total input: uncached + cached + cache_write = 20M
    assert tb.total_input == 20000000
    # Output tokens: 133.5K, NOT 267K
    assert tb.output == 133500
    assert tb.total_tokens == 20133500

