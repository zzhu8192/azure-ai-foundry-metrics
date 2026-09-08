"""Aggregator orchestrating resource discovery, metrics querying, pricing, and cost calculations."""

from typing import Dict, List, Optional
from azure.core.credentials import TokenCredential

from foundry_metrics.cost_management import CostManagementClient
from foundry_metrics.discovery import ResourceDiscovery
from foundry_metrics.models import (
    CostBreakdown,
    ModelUsage,
    PeriodUsage,
    ResourceInfo,
    TokenBreakdown,
)
from foundry_metrics.monitor import MonitorMetricsClient
from foundry_metrics.pricing import PricingManager
from foundry_metrics.time_windows import WindowBounds, calculate_windows


class MetricsAggregator:
    """Collects and reconciles token and cost metrics across Azure AI Foundry resources."""

    def __init__(
        self,
        credential: TokenCredential,
        pricing_manager: Optional[PricingManager] = None,
        enable_cost_management: bool = True,
    ):
        self.credential = credential
        self.pricing = pricing_manager or PricingManager()
        self.enable_cost_management = enable_cost_management
        self.discovery = ResourceDiscovery(credential)
        self.monitor_client = MonitorMetricsClient(credential)
        self.cost_client = CostManagementClient(credential)

    def collect_all_periods(
        self,
        subscription_ids: Optional[List[str]] = None,
        target_rg: Optional[str] = None,
        target_name: Optional[str] = None,
        timezone_name: str = "local",
        periods_to_run: Optional[List[str]] = None,
    ) -> List[PeriodUsage]:
        """Collect usage and cost data for requested time windows.

        Args:
            subscription_ids: Optional list of subscription IDs to limit search.
            target_rg: Optional resource group name filter.
            target_name: Optional resource name filter.
            timezone_name: "local" or "utc".
            periods_to_run: List of period keys to query (e.g. ["today", "week", "month"]).
        """
        windows = calculate_windows(tz_name=timezone_name)
        active_periods = periods_to_run or ["today", "week", "month"]

        # 1. Discover all AI resources
        resources = self.discovery.discover_resources(
            subscription_ids=subscription_ids,
            target_rg=target_rg,
            target_name=target_name,
        )

        results: List[PeriodUsage] = []

        for p_key in active_periods:
            if p_key not in windows:
                continue
            w = windows[p_key]
            period_usage = self._collect_single_period(w, resources)
            results.append(period_usage)

        return results

    def _collect_single_period(
        self,
        window: WindowBounds,
        resources: List[ResourceInfo],
    ) -> PeriodUsage:
        """Collect metrics and calculate costs for a single time window."""
        period = PeriodUsage(
            period_name=window.name,
            start_time=window.start,
            end_time=window.end,
            timezone_name=window.timezone_name,
        )

        # 1. Query Azure Monitor Metrics for each resource
        for res in resources:
            dep_tokens = self.monitor_client.query_resource_tokens(res, window)

            # Map deployment tokens to model usages
            for dep_name, tokens in dep_tokens.items():
                # Find matching model name from resource deployment metadata
                model_name = dep_name
                for d in res.deployments:
                    if d.name.lower() == dep_name.lower() and d.model_name:
                        model_name = d.model_name
                        break

                uncached_c, cached_c, write_c, out_c, savings = self.pricing.calculate_cost(
                    model_name=model_name,
                    uncached_input=tokens.uncached_input,
                    cached_input=tokens.cached_input,
                    output=tokens.output,
                    cache_write=tokens.cache_write,
                )

                cost_obj = CostBreakdown(
                    uncached_input_cost=uncached_c,
                    cached_input_cost=cached_c,
                    cache_write_cost=write_c,
                    output_cost=out_c,
                    currency="USD",
                )

                model_key = f"{res.name}/{dep_name}"
                period.models[model_key] = ModelUsage(
                    model_name=model_name,
                    deployment_name=dep_name,
                    resource_name=res.name,
                    subscription_id=res.subscription_id,
                    resource_group=res.resource_group,
                    tokens=tokens,
                    cost=cost_obj,
                    cache_savings=savings,
                )

        # 2. Optionally query Azure Cost Management
        if self.enable_cost_management and resources:
            distinct_subs = list(set(r.subscription_id for r in resources))
            for sub_id in distinct_subs:
                billed_costs, note = self.cost_client.query_subscription_costs(sub_id, window)
                if note:
                    period.cost_management_notes = note

                if billed_costs:
                    period.cost_management_available = True
                    # Reconcile billed costs to models
                    for res in resources:
                        if res.subscription_id == sub_id:
                            res_cost = billed_costs.get(res.id.lower(), 0.0)
                            if res_cost > 0.0:
                                # Distribute across deployments proportionally or assign
                                res_models = [m for m in period.models.values() if m.resource_name == res.name]
                                if res_models:
                                    tot_est = sum(m.cost.estimated_total_cost for m in res_models)
                                    for m in res_models:
                                        if tot_est > 0:
                                            ratio = m.cost.estimated_total_cost / tot_est
                                            m.cost.billed_cost = res_cost * ratio
                                        else:
                                            m.cost.billed_cost = res_cost / len(res_models)

        # 3. Calculate period totals
        period.recalculate_totals()
        return period
