"""Collect live metrics, portal estimates, and separate daily billing data."""

from datetime import timedelta
from typing import Dict, List, Optional
from azure.core.credentials import TokenCredential

from foundry_metrics.cost_management import BilledUsageRow, CostManagementClient, match_meter_to_model
from foundry_metrics.discovery import ResourceDiscovery
from foundry_metrics.models import CostBreakdown, ModelUsage, PeriodUsage, ResourceInfo, TokenBreakdown
from foundry_metrics.monitor import MonitorMetricsClient
from foundry_metrics.pricing import PricingManager
from foundry_metrics.time_windows import WindowBounds, calculate_windows


class MetricsAggregator:
    def __init__(self, credential: TokenCredential, pricing_manager: Optional[PricingManager] = None,
                 enable_cost_management: bool = True):
        self.credential = credential
        self.pricing = pricing_manager or PricingManager()
        self.enable_cost_management = enable_cost_management
        self.discovery = ResourceDiscovery(credential)
        self.monitor_client = MonitorMetricsClient(credential)
        self.cost_client = CostManagementClient(credential)

    def collect_all_periods(
        self, subscription_ids: Optional[List[str]] = None, target_rg: Optional[str] = None,
        target_name: Optional[str] = None, timezone_name: str = "local",
        periods_to_run: Optional[List[str]] = None, target_deployment: Optional[str] = None,
        start: Optional[str] = None, end: Optional[str] = None,
        daily: bool = False, period: str = "all",
    ) -> List[PeriodUsage]:
        windows = calculate_windows(
            tz_name=timezone_name, start=start, end=end, daily=daily, period=period,
        )
        if start is not None:
            active_periods = list(windows.keys()) if daily else ["custom"]
        elif daily:
            active_periods = list(windows.keys())
        elif period.lower() == "all":
            active_periods = periods_to_run or ["today", "week", "month"]
        else:
            active_periods = periods_to_run or [period.lower()]
        resources = self.discovery.discover_resources(
            subscription_ids=subscription_ids, target_rg=target_rg, target_name=target_name,
        )
        cost_rows_by_sub: Dict[str, List[BilledUsageRow]] = {}
        cost_note_by_sub: Dict[str, str] = {}
        if self.enable_cost_management and resources:
            selected = [windows[k] for k in active_periods if k in windows]
            if selected:
                overall = WindowBounds("Overall", min(w.start for w in selected),
                                       max(w.end for w in selected), selected[0].timezone_name)
                for sub_id in sorted({r.subscription_id for r in resources if r.subscription_id}):
                    rows, note = self.cost_client.query_subscription_billed_rows(sub_id, overall)
                    cost_rows_by_sub[sub_id] = rows
                    if note:
                        cost_note_by_sub[sub_id] = note
        return [self._collect_single_period(windows[key], resources, cost_rows_by_sub,
                                            cost_note_by_sub, target_deployment)
                for key in active_periods if key in windows]

    def _collect_single_period(
        self, window: WindowBounds, resources: List[ResourceInfo],
        cost_rows_by_sub: Optional[Dict[str, List[BilledUsageRow]]] = None,
        cost_note_by_sub: Optional[Dict[str, str]] = None,
        target_deployment: Optional[str] = None,
    ) -> PeriodUsage:
        period = PeriodUsage(window.name, window.start, window.end, window.timezone_name)
        fallback = False
        for resource in resources:
            metrics = self.monitor_client.query_resource_metrics(resource, window)
            period.estimate_notes.extend(metrics.notes)
            for deployment in sorted(metrics.tokens.keys() | metrics.estimated_costs.keys()):
                if target_deployment and deployment.lower() != target_deployment.lower():
                    continue
                model_name = next((d.model_name for d in resource.deployments
                                   if d.name.lower() == deployment.lower() and d.model_name), deployment)
                tokens = metrics.tokens.get(deployment, TokenBreakdown())
                if tokens.total_difference:
                    period.estimate_notes.append(
                        f"{resource.name}/{deployment}: TotalTokens reports {tokens.total_tokens:,} "
                        f"while input + output reports {tokens.component_total_tokens:,}. "
                        "Displaying the reported total; the difference is not assigned to input/output or priced."
                    )
                uncached, cached, write, output, savings = self.pricing.calculate_cost(
                    model_name, tokens.uncached_input, tokens.cached_input, tokens.output, tokens.cache_write,
                )
                portal_cost = metrics.estimated_costs.get(deployment)
                fallback |= portal_cost is None
                cost = CostBreakdown(
                    uncached_input_cost=uncached, cached_input_cost=cached, cache_write_cost=write,
                    output_cost=output, foundry_estimated_cost=portal_cost,
                    estimate_source="foundry_metric" if portal_cost is not None else "rate_card",
                )
                # Resource IDs avoid collisions across subscriptions and groups.
                key = f"{resource.id.lower()}/{deployment}"
                period.models[key] = ModelUsage(
                    model_name, deployment, resource.name, resource.subscription_id,
                    resource.resource_group, tokens, cost, savings,
                    metric_totals=metrics.metric_totals.get(deployment, {}),
                )
        if fallback:
            period.estimate_notes.append(
                "Foundry estimated cost unavailable for some deployments; using short-context "
                "rate-card prices and approximate cache counts. Cache-write telemetry may be missing."
            )
        if self.enable_cost_management and resources:
            start_day = int(window.start_utc.strftime("%Y%m%d"))
            end_day = int((window.end_utc - timedelta(microseconds=1)).strftime("%Y%m%d"))
            if cost_rows_by_sub is None:
                cost_rows_by_sub = {}
                cost_note_by_sub = {}
                for sub_id in sorted({r.subscription_id for r in resources if r.subscription_id}):
                    rows, note = self.cost_client.query_subscription_billed_rows(sub_id, window)
                    cost_rows_by_sub[sub_id] = rows
                    if note:
                        cost_note_by_sub[sub_id] = note
            rows = [r for group in cost_rows_by_sub.values() for r in group
                    if start_day <= r.usage_date <= end_day]
            period.cost_management_notes = " ".join(dict.fromkeys((cost_note_by_sub or {}).values())) or None
            self._apply_billing(period, resources, rows, target_deployment)
        period.estimate_notes = list(dict.fromkeys(period.estimate_notes))
        period.recalculate_totals()
        return period

    @staticmethod
    def _apply_billing(period: PeriodUsage, resources: List[ResourceInfo],
                       rows: List[BilledUsageRow], target_deployment: Optional[str]) -> None:
        """Attach billed dollars without replacing live tokens or pricing estimates.

        Billing meters identify models, not deployments. Assign a model's cost only
        if it has exactly one deployment; never copy it to multiple deployments.
        """
        for resource in resources:
            resource_rows = [r for r in rows if r.resource_id.lower() == resource.id.lower() and r.currency == "USD"]
            if not resource_rows:
                continue
            period.cost_management_available = True
            models = [m for key, m in period.models.items() if key.startswith(resource.id.lower() + "/")]
            names = list(dict.fromkeys([d.model_name for d in resource.deployments] + [m.model_name for m in models]))
            for row in resource_rows:
                matched = match_meter_to_model(row.meter, names)
                candidates = [m for m in models if m.model_name == matched]
                deployed = [d for d in resource.deployments if d.model_name == matched]
                if matched and len(candidates) == 1 and len(deployed) <= 1:
                    cost = candidates[0].cost
                    cost.billed_cost = (cost.billed_cost or 0.0) + row.cost
                elif not target_deployment:
                    period.unattributed_billed_cost += row.cost
            period.cost_management_notes = (
                "Azure Cost Management: delayed, full UTC-day charges; shown separately from live estimates. "
                "Shared-model or unmatched charges are not allocated to individual deployments."
            )
