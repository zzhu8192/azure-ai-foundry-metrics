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
