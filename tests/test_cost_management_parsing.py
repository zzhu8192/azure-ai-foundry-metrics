"""Unit tests for Azure Cost Management API response parsing."""

from unittest.mock import MagicMock
from foundry_metrics.cost_management import CostManagementClient


def test_parse_cost_management_rows():
    mock_cred = MagicMock()
    client = CostManagementClient(mock_cred)

    mock_resp = {
        "properties": {
            "columns": [
                {"name": "Cost", "type": "Number"},
                {"name": "ResourceId", "type": "String"},
                {"name": "Meter", "type": "String"},
                {"name": "Currency", "type": "String"},
            ],
            "rows": [
                [
                    42.50,
                    "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
                    "Standard GPT-4o Input Tokens",
                    "USD",
                ],
                [
                    18.75,
                    "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
                    "Standard GPT-4o Output Tokens",
                    "USD",
                ],
            ],
        }
    }

    costs = client._parse_cost_response(mock_resp)
    target_key = "/subscriptions/sub1/resourcegroups/rg1/providers/microsoft.cognitiveservices/accounts/acc1"
    assert target_key in costs
    assert round(costs[target_key], 2) == 61.25
