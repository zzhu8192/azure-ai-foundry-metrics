"""Azure Monitor Metrics API client for token usage and caching telemetry."""

from typing import Dict, List, Optional
import requests
from azure.core.credentials import TokenCredential

from foundry_metrics.models import ResourceInfo, TokenBreakdown
from foundry_metrics.time_windows import WindowBounds


METRICS_TO_QUERY = [
    "ProcessedPromptTokens",
    "GeneratedTokens",
    "cacheReadInputTokens",
    "AzureOpenAIContextTokensCacheMatchRate",
    "ephemeral1hInputTokens",
    "ephemeral5mInputTokens",
    "InputTokens",
    "OutputTokens",
    "TotalTokens",
    "ActiveTokens",
    "TokenTransaction",
]


class MonitorMetricsClient:
    """Queries Azure Monitor for model token and prompt cache telemetry."""

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

    def query_resource_tokens(
        self,
        resource: ResourceInfo,
        window: WindowBounds,
    ) -> Dict[str, TokenBreakdown]:
        """Query Azure Monitor metrics for a specific resource during a time window.

        Returns a dictionary mapping deployment_name (or model_name) to TokenBreakdown.
        """
        headers = self._get_auth_header()
        metric_str = ",".join(METRICS_TO_QUERY)
        api_version = "2024-02-01"

        url = f"https://management.azure.com{resource.id}/providers/Microsoft.Insights/metrics"
        params = {
            "api-version": api_version,
            "metricnames": metric_str,
            "timespan": window.azure_timespan,
            "interval": "PT1H",
            "aggregation": "Total,Average",
            "$filter": "ModelDeploymentName eq '*'",
        }

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 401:
                # Refresh token once
                self._token = None
                headers = self._get_auth_header()
                resp = requests.get(url, headers=headers, params=params, timeout=30)

            if not resp.ok:
                # Retry with ModelName filter or no filter if dimension filter isn't accepted
                params["$filter"] = "ModelName eq '*'"
                resp = requests.get(url, headers=headers, params=params, timeout=30)

            if not resp.ok:
                # Retry without dimension filter
                params.pop("$filter", None)
                resp = requests.get(url, headers=headers, params=params, timeout=30)

            if not resp.ok:
                print(f"Warning: Azure Monitor query failed for {resource.name} ({resp.status_code}): {resp.text[:200]}")
                return {}

            data = resp.json()
            return self._parse_metrics_response(data, resource)

        except Exception as exc:
            print(f"Error querying monitor metrics for {resource.name}: {exc}")
            return {}

    def _parse_metrics_response(
        self,
        response_json: dict,
        resource: ResourceInfo,
    ) -> Dict[str, TokenBreakdown]:
        """Parse raw metrics JSON into deployment-keyed TokenBreakdowns."""
        deployments_data: Dict[str, dict] = {}

        # Pre-seed with known deployments if available
        for dep in resource.deployments:
            deployments_data[dep.name] = {
                "prompt_tokens": 0,
                "generated_tokens": 0,
                "cache_read_tokens": 0,
                "cache_match_rate": 0.0,
                "cache_write_tokens": 0,
                "hourly_prompts": {},
                "hourly_cache_rates": {},
                "model_name": dep.model_name,
            }

        metric_items = response_json.get("value", [])
        for metric in metric_items:
            m_name = metric.get("name", {}).get("value", "")
            timeseries = metric.get("timeseries", [])

            for ts in timeseries:
                # Extract dimensions with case-insensitive matching
                metadata = ts.get("metadatavalues", [])
                dep_name = "default"
                model_name = ""

                for meta in metadata:
                    name_val = meta.get("name", {}).get("value", "").lower()
                    if name_val in ("modeldeploymentname", "deploymentname"):
                        dep_name = meta.get("value", dep_name)
                    elif name_val in ("modelname",):
                        model_name = meta.get("value", model_name)

                if dep_name not in deployments_data:
                    deployments_data[dep_name] = {
                        "prompt_tokens": 0,
                        "generated_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_match_rate": 0.0,
                        "cache_write_tokens": 0,
                        "hourly_prompts": {},
                        "hourly_cache_rates": {},
                        "model_name": model_name or dep_name,
                    }

                # Sum data points
                points = ts.get("data", [])
                total_sum = sum(p.get("total", 0.0) or 0.0 for p in points)
                avg_val = 0.0
                pts_with_avg = [p.get("average") for p in points if p.get("average") is not None]
                if pts_with_avg:
                    avg_val = sum(pts_with_avg) / len(pts_with_avg)

                entry = deployments_data[dep_name]
                if model_name and not entry["model_name"]:
                    entry["model_name"] = model_name

                if m_name in ("ProcessedPromptTokens", "InputTokens"):
                    entry["prompt_tokens"] += int(total_sum)
                    for p in points:
                        t_stamp = p.get("timeStamp")
                        p_tot = p.get("total", 0.0) or 0.0
                        if t_stamp and p_tot > 0:
                            entry["hourly_prompts"][t_stamp] = p_tot

                elif m_name in ("GeneratedTokens", "OutputTokens"):
                    entry["generated_tokens"] += int(total_sum)

                elif m_name in ("cacheReadInputTokens",):
                    entry["cache_read_tokens"] += int(total_sum)

                elif m_name in ("AzureOpenAIContextTokensCacheMatchRate",):
                    entry["cache_match_rate"] = max(entry["cache_match_rate"], avg_val)
                    for p in points:
                        t_stamp = p.get("timeStamp")
                        p_avg = p.get("average")
                        if t_stamp and p_avg is not None and p_avg > 0:
                            entry["hourly_cache_rates"][t_stamp] = p_avg

                elif m_name in ("ephemeral1hInputTokens", "ephemeral5mInputTokens"):
                    entry["cache_write_tokens"] += int(total_sum)

        # Build TokenBreakdown objects
        result: Dict[str, TokenBreakdown] = {}
        for dep_name, vals in deployments_data.items():
            prompt_tokens = vals["prompt_tokens"]
            generated_tokens = vals["generated_tokens"]
            cache_read = vals["cache_read_tokens"]
            match_rate = vals["cache_match_rate"]
            cache_write = vals["cache_write_tokens"]
            hourly_prompts = vals["hourly_prompts"]
            hourly_cache_rates = vals["hourly_cache_rates"]

            # Compute cached input tokens with weighted hourly precision
            if cache_read > 0:
                cached_input = cache_read
            elif hourly_cache_rates and hourly_prompts:
                weighted_cached = 0.0
                for t_stamp, rate in hourly_cache_rates.items():
                    p_tokens = hourly_prompts.get(t_stamp, 0.0)
                    weighted_cached += p_tokens * (rate / 100.0)
                cached_input = int(weighted_cached)
            elif match_rate > 0.0 and prompt_tokens > 0:
                cached_input = int(prompt_tokens * (match_rate / 100.0))
            else:
                cached_input = 0

            uncached_input = max(0, prompt_tokens - cached_input)

            if uncached_input > 0 or cached_input > 0 or generated_tokens > 0 or cache_write > 0:
                result[dep_name] = TokenBreakdown(
                    uncached_input=uncached_input,
                    cached_input=cached_input,
                    cache_write=cache_write,
                    output=generated_tokens,
                )

        return result
