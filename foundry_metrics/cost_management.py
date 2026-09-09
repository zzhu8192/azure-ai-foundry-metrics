"""Azure Cost Management API client for querying invoiced / metered billing costs."""

import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import requests
from azure.core.credentials import TokenCredential

from foundry_metrics.time_windows import WindowBounds


AI_SERVICE_NAMES = [
    "Azure OpenAI",
    "Azure Cognitive Services",
    "Cognitive Services",
    "Azure AI Foundry",
    "Foundry Models",
    "Azure Applied AI Services",
    "Azure AI Services",
]


@dataclass
class BilledUsageRow:
    """Single daily billed usage row from Azure Cost Management."""
    resource_id: str
    meter: str
    usage_date: int  # YYYYMMDD
    cost: float
    usage_quantity_1m: float  # in 1M tokens
    currency: str = "USD"


def match_meter_to_model(meter_name: str, model_names: List[str]) -> Optional[str]:
    """Match an Azure Cost Management meter name to a deployed model name.

    Examples:
        '5.6 terra ShortCo Inp Std Gl 1M Tokens' -> 'gpt-5.6-terra'
        '5.6 sol ShortCo Cd Wr Std Gl 1M Tokens' -> 'gpt-5.6-sol'
        '5.6 luna ShortCo Opt Std Gl 1M Tokens' -> 'gpt-5.6-luna'
        'gpt 4o mini dev ft cchd inpt glbl Tokens' -> 'gpt-4o-mini'
    """
    meter_norm = re.sub(r"[-_]", " ", meter_name.lower())
    for m in sorted(model_names, key=len, reverse=True):
        m_norm = re.sub(r"[-_]", " ", m.lower())
        m_parts = [p for p in m_norm.split() if p not in ("gpt", "azure", "openai", "model")]
        core_str = " ".join(m_parts)
        if m_norm in meter_norm or (core_str and core_str in meter_norm):
            return m
    return None


def categorize_meter_tokens(meter_name: str, usage_quantity_1m: float) -> Tuple[str, int]:
    """Categorize an Azure meter name into token category and integer token count.

    Returns:
        (category, token_count) where category in:
        "cached_input", "cache_write", "uncached_input", "output", "other"
    """
    m = meter_name.lower()
    tokens = int(round(usage_quantity_1m * 1_000_000))
    if any(k in m for k in ("cd inp", "cchd inpt", "cchd inp", "cached input", "cd_inp")):
        return "cached_input", tokens
    elif any(k in m for k in ("cd wr", "cache write", "cd_wr")):
        return "cache_write", tokens
    elif any(k in m for k in ("opt", "outp", "output")):
        return "output", tokens
    elif any(k in m for k in ("inp", "inpt", "input")):
        return "uncached_input", tokens
    return "other", tokens


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

    def query_subscription_billed_rows(
        self,
        subscription_id: str,
        window: WindowBounds,
        max_retries: int = 3,
    ) -> Tuple[List[BilledUsageRow], Optional[str]]:
        """Query daily billed usage records for AI services in a subscription.

        Returns:
            (billed_rows, optional_warning_or_note)
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
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    },
                    "totalUsage": {
                        "name": "UsageQuantity",
                        "function": "Sum",
                    },
                },
                "filter": {
                    "dimensions": {
                        "name": "ServiceName",
                        "operator": "In",
                        "values": AI_SERVICE_NAMES,
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

        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code == 401:
                    self._token = None
                    headers = self._get_auth_header()
                    resp = requests.post(url, headers=headers, json=payload, timeout=30)

                if resp.status_code == 403:
                    return [], (
                        "Cost Management API: 403 Forbidden (requires 'Cost Management Reader' role). "
                        "Displaying real-time Azure Monitor metrics pricing."
                    )

                if resp.status_code == 429:
                    retry_header = resp.headers.get(
                        "x-ms-ratelimit-microsoft.costmanagement-clienttype-retry-after",
                        resp.headers.get("Retry-After", "10"),
                    )
                    try:
                        retry_after = max(1, min(int(retry_header), 35))
                    except (ValueError, TypeError):
                        retry_after = 10

                    if attempt < max_retries:
                        time.sleep(retry_after + 2)
                        continue
                    else:
                        return [], (
                            "Cost Management query rate limit reached (429). "
                            "Displaying real-time Azure Monitor metrics pricing."
                        )

                if not resp.ok:
                    return [], f"Cost Management query returned {resp.status_code}: {resp.text[:150]}"

                data = resp.json()
                rows = self._parse_billed_rows(data)
                note = "Official Azure Cost Management data."
                return rows, note

            except Exception as exc:
                return [], f"Could not connect to Cost Management API: {exc}"

        return [], "Cost Management query timed out."

    def query_subscription_costs(
        self,
        subscription_id: str,
        window: WindowBounds,
    ) -> Tuple[Dict[str, float], Optional[str]]:
        """Query actual costs for Azure OpenAI / Cognitive Services in a subscription.

        Maintained for backwards compatibility with resource-level cost mappings.
        Returns:
            (costs_by_resource_id, optional_warning_or_note)
        """
        rows, note = self.query_subscription_billed_rows(subscription_id, window)
        costs: Dict[str, float] = {}
        for r in rows:
            costs[r.resource_id] = costs.get(r.resource_id, 0.0) + r.cost
        return costs, note

    def _parse_billed_rows(self, response_json: dict) -> List[BilledUsageRow]:
        """Parse raw Cost Management response into structured BilledUsageRow list."""
        parsed_rows: List[BilledUsageRow] = []
        raw_rows = response_json.get("properties", {}).get("rows", [])
        columns = [c.get("name") for c in response_json.get("properties", {}).get("columns", [])]

        cost_idx = columns.index("Cost") if "Cost" in columns else 0
        usage_idx = columns.index("UsageQuantity") if "UsageQuantity" in columns else -1
        date_idx = columns.index("UsageDate") if "UsageDate" in columns else -1
        res_idx = columns.index("ResourceId") if "ResourceId" in columns else 1
        meter_idx = columns.index("Meter") if "Meter" in columns else 2
        curr_idx = columns.index("Currency") if "Currency" in columns else -1

        for row in raw_rows:
            if len(row) > max(cost_idx, res_idx, meter_idx):
                cost_val = float(row[cost_idx] or 0.0)
                usage_val = float(row[usage_idx] or 0.0) if usage_idx >= 0 and usage_idx < len(row) else 0.0
                date_val = int(row[date_idx]) if date_idx >= 0 and date_idx < len(row) and row[date_idx] is not None else 0
                res_id = str(row[res_idx] or "").lower()
                meter_val = str(row[meter_idx] or "")
                curr_val = str(row[curr_idx] or "USD") if curr_idx >= 0 and curr_idx < len(row) else "USD"

                parsed_rows.append(
                    BilledUsageRow(
                        resource_id=res_id,
                        meter=meter_val,
                        usage_date=date_val,
                        cost=cost_val,
                        usage_quantity_1m=usage_val,
                        currency=curr_val,
                    )
                )

        return parsed_rows

    def _parse_cost_response(self, response_json: dict) -> Dict[str, float]:
        """Parse Cost Management query response rows into resource-level costs."""
        costs: Dict[str, float] = {}
        raw_rows = response_json.get("properties", {}).get("rows", [])
        columns = [c.get("name") for c in response_json.get("properties", {}).get("columns", [])]

        cost_idx = columns.index("Cost") if "Cost" in columns else 0
        res_idx = columns.index("ResourceId") if "ResourceId" in columns else 1

        for row in raw_rows:
            if len(row) > max(cost_idx, res_idx):
                cost_val = float(row[cost_idx] or 0.0)
                res_id = str(row[res_idx] or "").lower()
                costs[res_id] = costs.get(res_id, 0.0) + cost_val

        return costs
