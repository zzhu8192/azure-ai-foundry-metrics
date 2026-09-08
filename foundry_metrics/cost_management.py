"""Azure Cost Management API client for querying invoiced / metered billing costs."""

from typing import Dict, List, Optional, Tuple
import requests
from azure.core.credentials import TokenCredential

from foundry_metrics.time_windows import WindowBounds


class CostManagementClient:
    """Queries Azure Cost Management API for official billed costs and credits usage."""

    def __init__(self, credential: TokenCredential):
        self.credential = credential
        self._token: Optional[str] = None

    def _get_auth_header(self) -> Dict[str, str]:
        if not self._token:
            token_obj = self.credential.get_token("https://management.azure.com/.default")
            self._token = token_obj.token
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def query_subscription_costs(
        self,
        subscription_id: str,
        window: WindowBounds,
    ) -> Tuple[Dict[str, float], Optional[str]]:
        """Query actual costs for Azure OpenAI / Cognitive Services in a subscription.

        Returns:
            (costs_by_resource_id, optional_warning_or_note)
        """
        headers = self._get_auth_header()
        api_version = "2023-11-01"
        url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version={api_version}"

        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": window.cost_management_period["from"],
                "to": window.cost_management_period["to"],
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "filter": {
                    "dimensions": {
                        "name": "ServiceName",
                        "operator": "In",
                        "values": [
                            "Azure OpenAI",
                            "Azure Cognitive Services",
                            "Cognitive Services",
                            "Azure AI Foundry",
                        ],
                    }
                },
                "grouping": [
                    {
                        "type": "Dimension",
                        "name": "ResourceId",
                    },
                    {
                        "type": "Dimension",
                        "name": "Meter",
                    },
                ],
            },
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 401:
                self._token = None
                headers = self._get_auth_header()
                resp = requests.post(url, headers=headers, json=payload, timeout=30)

            if resp.status_code == 403:
                return {}, (
                    "Cost Management API: 403 Forbidden (requires 'Cost Management Reader' role). "
                    "Displaying real-time Azure Monitor metrics pricing."
                )

            if not resp.ok:
                return {}, f"Cost Management query returned {resp.status_code}: {resp.text[:150]}"

            data = resp.json()
            costs_by_resource = self._parse_cost_response(data)
            note = "Official Azure Cost Management data (subject to 4-24 hr ingestion latency)."
            return costs_by_resource, note

        except Exception as exc:
            return {}, f"Could not connect to Cost Management API: {exc}"

    def _parse_cost_response(self, response_json: dict) -> Dict[str, float]:
        """Parse Cost Management query response rows."""
        costs: Dict[str, float] = {}
        rows = response_json.get("properties", {}).get("rows", [])
        columns = [c.get("name") for c in response_json.get("properties", {}).get("columns", [])]

        cost_idx = columns.index("Cost") if "Cost" in columns else 0
        res_idx = columns.index("ResourceId") if "ResourceId" in columns else 1

        for row in rows:
            if len(row) > max(cost_idx, res_idx):
                cost_val = float(row[cost_idx] or 0.0)
                res_id = str(row[res_idx] or "").lower()
                costs[res_id] = costs.get(res_id, 0.0) + cost_val

        return costs
