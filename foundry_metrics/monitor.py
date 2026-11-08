"""Live token telemetry and Foundry's estimated USD cost from Azure Monitor."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
from azure.core.credentials import TokenCredential

from foundry_metrics.models import ResourceInfo, TokenBreakdown
from foundry_metrics.time_windows import WindowBounds

logger = logging.getLogger(__name__)
COST_METRIC = "FoundryModelEstimatedCost"
CACHE_RATE_METRICS = ("AzureOpenAIContextTokensCacheMatchRate", "TokensCacheMatchRate")
METRICS_TO_QUERY = [
    "InputTokens", "OutputTokens", "ProcessedPromptTokens", "GeneratedTokens",
    "cacheReadInputTokens", "ephemeral1hInputTokens", "ephemeral5mInputTokens",
    *CACHE_RATE_METRICS, COST_METRIC,
]


@dataclass
class ResourceMetrics:
    tokens: Dict[str, TokenBreakdown] = field(default_factory=dict)
    estimated_costs: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


class MonitorMetricsClient:
    def __init__(self, credential: TokenCredential):
        self.credential = credential
        self._token: Optional[str] = None
        self._definitions: Dict[str, Optional[dict]] = {}

    def _get_auth_header(self) -> Dict[str, str]:
        if not self._token:
            self._token = self.credential.get_token("https://management.azure.com/.default").token
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _get(self, url: str, params: dict):
        response = requests.get(url, headers=self._get_auth_header(), params=params, timeout=30)
        if response.status_code == 401:
            self._token = None
            response = requests.get(url, headers=self._get_auth_header(), params=params, timeout=30)
        return response

    def _metric_definitions(self, resource: ResourceInfo) -> Optional[dict]:
        if resource.id not in self._definitions:
            definitions = None
            try:
                url = f"https://management.azure.com{resource.id}/providers/Microsoft.Insights/metricDefinitions"
                response = self._get(url, {"api-version": "2018-01-01"})
                if response.ok:
                    definitions = {m["name"]["value"]: m for m in response.json().get("value", [])}
            except requests.RequestException:
                pass
            self._definitions[resource.id] = definitions
        return self._definitions[resource.id]

    def query_resource_metrics(self, resource: ResourceInfo, window: WindowBounds) -> ResourceMetrics:
        result = ResourceMetrics()
        try:
            definitions = self._metric_definitions(resource)
            groups = {}
            for name in METRICS_TO_QUERY:
                aggregation = "Average" if name in CACHE_RATE_METRICS else "Total"
                dimension = "ModelDeploymentName"
                if definitions is not None:
                    definition = definitions.get(name)
                    if definition is None:
                        continue
                    supported = definition.get("supportedAggregationTypes", [])
                    if supported and aggregation not in supported:
                        continue
                    dims = {d["value"].lower(): d["value"] for d in definition.get("dimensions", [])}
                    dimension = dims.get("modeldeploymentname", dims.get("modelname"))
                    if dimension is None and len(resource.deployments) != 1:
                        result.notes.append(f"{resource.name}: {name} has no deployment dimension; skipped.")
                        continue
                groups.setdefault((aggregation, dimension), []).append(name)

            raw = {"value": []}
            url = f"https://management.azure.com{resource.id}/providers/Microsoft.Insights/metrics"
            for (aggregation, dimension), names in groups.items():
                params = {
                    "api-version": "2024-02-01", "metricnames": ",".join(names),
                    "timespan": window.azure_timespan, "interval": "PT5M",
                    "aggregation": aggregation, "top": 10000,
                }
                if dimension:
                    params["$filter"] = f"{dimension} eq '*'"
                response = self._get(url, params)
                if response.ok:
                    raw["value"].extend(response.json().get("value", []))
                elif response.status_code == 400:
                    # An unavailable metric must not discard all token telemetry.
                    for name in names:
                        single_params = {**params, "metricnames": name}
                        single = self._get(url, single_params)
                        if not single.ok and definitions is None:
                            single_params["$filter"] = "ModelName eq '*'"
                            single = self._get(url, single_params)
                        if single.ok:
                            raw["value"].extend(single.json().get("value", []))
                        else:
                            result.notes.append(f"{resource.name}: {name} unavailable ({single.status_code}).")
                else:
                    result.notes.append(f"{resource.name}: Monitor query failed ({response.status_code}).")
            result.tokens = self._parse_metrics_response(raw, resource)
            result.estimated_costs = self._parse_estimated_costs(raw, resource)
        except Exception as exc:
            logger.warning("Could not query Monitor for %s: %s", resource.name, exc)
            result.notes.append(f"{resource.name}: Monitor query unavailable.")
        return result

    def query_resource_tokens(self, resource: ResourceInfo, window: WindowBounds) -> Dict[str, TokenBreakdown]:
        return self.query_resource_metrics(resource, window).tokens

    @staticmethod
    def _series_key(series: dict, resource: ResourceInfo) -> str:
        dims = {m.get("name", {}).get("value", "").lower(): m.get("value", "")
                for m in series.get("metadatavalues", [])}
        deployment = dims.get("modeldeploymentname") or dims.get("deploymentname")
        if deployment:
            return next((d.name for d in resource.deployments if d.name.lower() == deployment.lower()), deployment)
        model = dims.get("modelname")
        if model:
            matches = [d for d in resource.deployments if d.model_name.lower() == model.lower()]
            return matches[0].name if len(matches) == 1 else model
        return resource.deployments[0].name if len(resource.deployments) == 1 else "default"

    def _collect_metrics(self, response_json: dict, resource: ResourceInfo) -> dict:
        deployments = {}
        for metric in response_json.get("value", []):
            if metric.get("errorCode") not in (None, "", "Success"):
                continue
            name = metric.get("name", {}).get("value", "")
            for series in metric.get("timeseries", []):
                key = self._series_key(series, resource)
                item = deployments.setdefault(key, {}).setdefault(name, {"points": [], "total": 0.0})
                item["points"].extend(series.get("data", []))
                item["total"] += sum(p.get("total") or 0 for p in series.get("data", []))
        return deployments

    def _parse_estimated_costs(self, response_json: dict, resource: ResourceInfo) -> Dict[str, float]:
        costs = {}
        for key, metrics in self._collect_metrics(response_json, resource).items():
            cost = metrics.get(COST_METRIC)
            # Missing points mean unavailable, while a reported zero is valid.
            if cost and any(p.get("total") is not None for p in cost["points"]):
                costs[key] = float(cost["total"])
        return costs

    def _parse_metrics_response(self, response_json: dict, resource: ResourceInfo) -> Dict[str, TokenBreakdown]:
        result = {}
        for key, metrics in self._collect_metrics(response_json, resource).items():
            def preferred(*names):
                available = [metrics[n] for n in names if n in metrics]
                return next((m for m in available if m["total"] > 0), available[0] if available else {"total": 0, "points": []})

            prompt = preferred("InputTokens", "ProcessedPromptTokens")
            prompt_tokens = int(prompt["total"])
            output = int(preferred("OutputTokens", "GeneratedTokens")["total"])
            cache_read = metrics.get("cacheReadInputTokens")
            cache_write = int(sum(metrics.get(n, {}).get("total", 0) for n in ("ephemeral1hInputTokens", "ephemeral5mInputTokens")))
            cached_input = 0
            if cache_read and any(p.get("total") is not None for p in cache_read["points"]):
                cached_input = int(cache_read["total"])
            else:
                cache_rate = next((metrics[n] for n in CACHE_RATE_METRICS if n in metrics), None)
                if cache_rate:
                    rates = {}
                    for p in cache_rate["points"]:
                        if p.get("average") is not None:
                            rates.setdefault(p.get("timeStamp"), []).append(p["average"])
                    buckets = {}
                    for p in prompt["points"]:
                        stamp = p.get("timeStamp")
                        buckets[stamp] = buckets.get(stamp, 0) + (p.get("total") or 0)
                    # Average match rates approximate cache counts. Missing
                    # buckets get no discount; use the cost metric when available.
                    cached_input = int(sum(
                        count * min(100, max(0, sum(rates[stamp]) / len(rates[stamp]))) / 100
                        for stamp, count in buckets.items() if stamp in rates
                    ))
            cached_input = min(max(0, cached_input), max(0, prompt_tokens))
            cache_write = min(max(0, cache_write), max(0, prompt_tokens - cached_input))
            tokens = TokenBreakdown(
                uncached_input=max(0, prompt_tokens - cached_input - cache_write),
                cached_input=cached_input, cache_write=cache_write, output=output,
            )
            if tokens.total_tokens:
                result[key] = tokens
        return result
