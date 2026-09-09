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


def test_match_meter_to_model():
    from foundry_metrics.cost_management import match_meter_to_model

    models = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-6-astra", "gpt-4o", "gpt-4o-mini"]

    assert match_meter_to_model("5.6 terra ShortCo Inp Std Gl 1M Tokens", models) == "gpt-5.6-terra"
    assert match_meter_to_model("5.6 sol ShortCo Cd Wr Std Gl 1M Tokens", models) == "gpt-5.6-sol"
    assert match_meter_to_model("5.6 luna ShortCo Opt Std Gl 1M Tokens", models) == "gpt-5.6-luna"
    assert match_meter_to_model("Standard GPT-4o Input Tokens", models) == "gpt-4o"
    assert match_meter_to_model("gpt 4o mini dev ft cchd inpt glbl Tokens", models) == "gpt-4o-mini"
    assert match_meter_to_model("Completely Unrelated Meter", models) is None


def test_categorize_meter_tokens():
    from foundry_metrics.cost_management import categorize_meter_tokens

    cat, tok = categorize_meter_tokens("5.6 terra ShortCo Cd Inp Std Gl 1M Tokens", 19.28)
    assert cat == "cached_input"
    assert tok == 19280000

    cat, tok = categorize_meter_tokens("5.6 terra ShortCo Cd Wr Std Gl 1M Tokens", 0.67)
    assert cat == "cache_write"
    assert tok == 670000

    cat, tok = categorize_meter_tokens("5.6 terra ShortCo Inp Std Gl 1M Tokens", 0.087)
    assert cat == "uncached_input"
    assert tok == 87000

    cat, tok = categorize_meter_tokens("5.6 terra ShortCo Opt Std Gl 1M Tokens", 0.133)
    assert cat == "output"
    assert tok == 133000

    cat, tok = categorize_meter_tokens("Some Unknown Feature Meter", 1.0)
    assert cat == "other"
    assert tok == 1000000


def test_parse_billed_rows():
    mock_cred = MagicMock()
    client = CostManagementClient(mock_cred)

    mock_resp = {
        "properties": {
            "columns": [
                {"name": "Cost", "type": "Number"},
                {"name": "UsageQuantity", "type": "Number"},
                {"name": "Meter", "type": "String"},
                {"name": "UsageDate", "type": "Number"},
                {"name": "ResourceId", "type": "String"},
                {"name": "Currency", "type": "String"},
            ],
            "rows": [
                [
                    7.32,
                    19.28,
                    "5.6 terra ShortCo Cd Inp Std Gl 1M Tokens",
                    20260908,
                    "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
                    "USD",
                ],
                [
                    16.23,
                    13.49,
                    "5.6 sol ShortCo Cd Inp Std Gl 1M Tokens",
                    20260908,
                    "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acc1",
                    "USD",
                ],
            ],
        }
    }

    rows = client._parse_billed_rows(mock_resp)
    assert len(rows) == 2
    assert rows[0].cost == 7.32
    assert rows[0].usage_quantity_1m == 19.28
    assert rows[0].meter == "5.6 terra ShortCo Cd Inp Std Gl 1M Tokens"
    assert rows[0].usage_date == 20260908
    assert rows[0].currency == "USD"
    assert rows[1].cost == 16.23

