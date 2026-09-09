"""Aggregator orchestrating resource discovery, metrics querying, pricing, and cost calculations."""

from typing import Dict, List, Optional
from azure.core.credentials import TokenCredential

from foundry_metrics.cost_management import (
    BilledUsageRow,
    CostManagementClient,
    categorize_meter_tokens,
    match_meter_to_model,
)
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

        # 2. Pre-fetch Cost Management data covering all requested periods in a single call
        cost_rows_by_sub: Dict[str, List[BilledUsageRow]] = {}
        cost_note_by_sub: Dict[str, str] = {}
        if self.enable_cost_management and resources:
            valid_windows = [windows[k] for k in active_periods if k in windows]
            if valid_windows:
                earliest_start = min(w.start for w in valid_windows)
                latest_end = max(w.end for w in valid_windows)
                overall_window = WindowBounds(
                    name="Overall",
                    start=earliest_start,
                    end=latest_end,
                    timezone_name=valid_windows[0].timezone_name,
                )
                distinct_subs = list(set(r.subscription_id for r in resources if r.subscription_id))
                for sub_id in distinct_subs:
                    rows, note = self.cost_client.query_subscription_billed_rows(sub_id, overall_window)
                    cost_rows_by_sub[sub_id] = rows
                    if note:
                        cost_note_by_sub[sub_id] = note

        results: List[PeriodUsage] = []

        for p_key in active_periods:
            if p_key not in windows:
                continue
            w = windows[p_key]
            period_usage = self._collect_single_period(
                window=w,
                resources=resources,
                cost_rows_by_sub=cost_rows_by_sub,
                cost_note_by_sub=cost_note_by_sub,
            )
            results.append(period_usage)

        return results

    def _collect_single_period(
        self,
        window: WindowBounds,
        resources: List[ResourceInfo],
        cost_rows_by_sub: Optional[Dict[str, List[BilledUsageRow]]] = None,
        cost_note_by_sub: Optional[Dict[str, str]] = None,
    ) -> PeriodUsage:
        """Collect metrics and calculate costs for a single time window."""
        period = PeriodUsage(
            period_name=window.name,
            start_time=window.start,
            end_time=window.end,
            timezone_name=window.timezone_name,
        )

        # 1. Query Azure Monitor Metrics for each resource (real-time telemetry)
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

        # 2. Reconcile with official Cost Management data if enabled
        if self.enable_cost_management and resources:
            start_date_int = int(window.start.strftime("%Y%m%d"))
            end_date_int = int(window.end.strftime("%Y%m%d"))

            all_period_rows: List[BilledUsageRow] = []
            if cost_rows_by_sub is not None:
                for rows in cost_rows_by_sub.values():
                    for r in rows:
                        if start_date_int <= r.usage_date <= end_date_int:
                            all_period_rows.append(r)
            else:
                # Standalone call fallback
                distinct_subs = list(set(r.subscription_id for r in resources if r.subscription_id))
                for sub_id in distinct_subs:
                    rows, note = self.cost_client.query_subscription_billed_rows(sub_id, window)
                    all_period_rows.extend(rows)
                    if note and not period.cost_management_notes:
                        period.cost_management_notes = note

            if cost_note_by_sub:
                for note in cost_note_by_sub.values():
                    if note and not period.cost_management_notes:
                        period.cost_management_notes = note

            if all_period_rows:
                period.cost_management_available = True
                period.cost_management_notes = "Official Azure Cost Management data."

                for res in resources:
                    res_id_lower = res.id.lower()
                    res_rows = [r for r in all_period_rows if r.resource_id == res_id_lower]
                    if not res_rows:
                        continue

                    # Candidate model/deployment names for this resource
                    model_names = [d.name for d in res.deployments] + [
                        d.model_name for d in res.deployments if d.model_name
                    ]
                    existing_res_models = {
                        k: m for k, m in period.models.items() if m.resource_name == res.name
                    }
                    for m in existing_res_models.values():
                        if m.model_name not in model_names:
                            model_names.append(m.model_name)
                        if m.deployment_name not in model_names:
                            model_names.append(m.deployment_name)

                    cost_by_model: Dict[str, float] = {}
                    tokens_by_model: Dict[str, Dict[str, int]] = {}
                    unmatched_cost = 0.0

                    for r in res_rows:
                        matched_target = match_meter_to_model(r.meter, model_names)
                        cat, tok_count = categorize_meter_tokens(r.meter, r.usage_quantity_1m)
                        if matched_target:
                            cost_by_model[matched_target] = (
                                cost_by_model.get(matched_target, 0.0) + r.cost
                            )
                            if matched_target not in tokens_by_model:
                                tokens_by_model[matched_target] = {
                                    "uncached_input": 0,
                                    "cached_input": 0,
                                    "cache_write": 0,
                                    "output": 0,
                                }
                            if cat in tokens_by_model[matched_target]:
                                tokens_by_model[matched_target][cat] += tok_count
                        else:
                            unmatched_cost += r.cost

                    # Assign matched costs and tokens to models
                    for model_key, m in list(existing_res_models.items()):
                        matched_name = None
                        if m.model_name in cost_by_model:
                            matched_name = m.model_name
                        elif m.deployment_name in cost_by_model:
                            matched_name = m.deployment_name

                        if matched_name:
                            m.cost.billed_cost = cost_by_model[matched_name]
                            if matched_name in tokens_by_model:
                                b_toks = tokens_by_model[matched_name]
                                m.tokens = TokenBreakdown(
                                    uncached_input=b_toks["uncached_input"],
                                    cached_input=b_toks["cached_input"],
                                    cache_write=b_toks["cache_write"],
                                    output=b_toks["output"],
                                )
                                # Recompute costs from rate card so estimated matches billed
                                uncached_c, cached_c, write_c, out_c, savings = self.pricing.calculate_cost(
                                    model_name=m.model_name,
                                    uncached_input=m.tokens.uncached_input,
                                    cached_input=m.tokens.cached_input,
                                    output=m.tokens.output,
                                    cache_write=m.tokens.cache_write,
                                )
                                m.cost.uncached_input_cost = uncached_c
                                m.cost.cached_input_cost = cached_c
                                m.cost.cache_write_cost = write_c
                                m.cost.output_cost = out_c
                                m.cache_savings = savings

                    # If there are models in billing that had no telemetry in Monitor
                    for matched_name, b_cost in cost_by_model.items():
                        already_assigned = any(
                            (m.model_name == matched_name or m.deployment_name == matched_name)
                            and m.resource_name == res.name
                            for m in period.models.values()
                        )
                        if not already_assigned and b_cost > 0:
                            model_key = f"{res.name}/{matched_name}"
                            b_toks = tokens_by_model.get(
                                matched_name,
                                {"uncached_input": 0, "cached_input": 0, "cache_write": 0, "output": 0},
                            )
                            new_toks = TokenBreakdown(
                                uncached_input=b_toks["uncached_input"],
                                cached_input=b_toks["cached_input"],
                                cache_write=b_toks["cache_write"],
                                output=b_toks["output"],
                            )
                            uncached_c, cached_c, write_c, out_c, savings = self.pricing.calculate_cost(
                                model_name=matched_name,
                                uncached_input=new_toks.uncached_input,
                                cached_input=new_toks.cached_input,
                                output=new_toks.output,
                                cache_write=new_toks.cache_write,
                            )
                            period.models[model_key] = ModelUsage(
                                model_name=matched_name,
                                deployment_name=matched_name,
                                resource_name=res.name,
                                subscription_id=res.subscription_id,
                                resource_group=res.resource_group,
                                tokens=new_toks,
                                cost=CostBreakdown(
                                    uncached_input_cost=uncached_c,
                                    cached_input_cost=cached_c,
                                    cache_write_cost=write_c,
                                    output_cost=out_c,
                                    billed_cost=b_cost,
                                    currency="USD",
                                ),
                                cache_savings=savings,
                            )

                    # If unmatched cost exists, distribute across existing models
                    if unmatched_cost > 0 and existing_res_models:
                        tot_est = sum(m.cost.estimated_total_cost for m in existing_res_models.values())
                        for m in existing_res_models.values():
                            extra = (
                                (unmatched_cost * (m.cost.estimated_total_cost / tot_est))
                                if tot_est > 0
                                else (unmatched_cost / len(existing_res_models))
                            )
                            m.cost.billed_cost = (m.cost.billed_cost or 0.0) + extra

            else:
                if window.name == "Today":
                    period.cost_management_notes = (
                        "Real-time estimated costs via Azure Monitor metrics (Cost Management pending 4-24hr lag)"
                    )

        # 3. Calculate period totals
        period.recalculate_totals()
        return period
