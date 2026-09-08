"""Mock data generator for demo mode and testing without Azure credentials."""

from datetime import datetime, timezone
from typing import List

from foundry_metrics.models import (
    CostBreakdown,
    ModelUsage,
    PeriodUsage,
    TokenBreakdown,
)
from foundry_metrics.pricing import PricingManager
from foundry_metrics.time_windows import calculate_windows


def get_demo_periods(timezone_name: str = "local") -> List[PeriodUsage]:
    """Generate realistic demo usage metrics for Today, Current Week, and Current Month."""
    windows = calculate_windows(tz_name=timezone_name)
    pricing = PricingManager()

    # Scale factors: today=1x, week=5.5x, month=18x
    scales = {
        "today": 1.0,
        "week": 5.5,
        "month": 18.2,
    }

    # Base daily token usage for various models
    base_models = [
        {
            "model_name": "gpt-4o",
            "deployment_name": "gpt-4o-prod",
            "resource_name": "ai-foundry-eus2-01",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 1_250_000,
            "cached_input": 3_750_000,  # 75% cache hit rate!
            "cache_write": 0,
            "output": 620_000,
        },
        {
            "model_name": "gpt-4o-mini",
            "deployment_name": "gpt-4o-mini-chat",
            "resource_name": "ai-foundry-eus2-01",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 8_400_000,
            "cached_input": 12_600_000,  # 60% cache hit rate
            "cache_write": 0,
            "output": 2_100_000,
        },
        {
            "model_name": "o3-mini",
            "deployment_name": "o3-mini-reasoning",
            "resource_name": "ai-foundry-eus2-01",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 650_000,
            "cached_input": 350_000,   # 35% cache hit rate
            "cache_write": 0,
            "output": 1_450_000,       # High reasoning output tokens
        },
        {
            "model_name": "claude-3-5-sonnet",
            "deployment_name": "claude-sonnet-az",
            "resource_name": "ai-foundry-wus3-02",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 920_000,
            "cached_input": 2_760_000,  # 75% prompt cache read
            "cache_write": 350_000,    # Cache creation writes
            "output": 410_000,
        },
        {
            "model_name": "deepseek-r1",
            "deployment_name": "deepseek-r1-model",
            "resource_name": "ai-foundry-wus3-02",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 1_800_000,
            "cached_input": 700_000,
            "cache_write": 0,
            "output": 2_950_000,
        },
        {
            "model_name": "text-embedding-3-large",
            "deployment_name": "text-embedding-v3",
            "resource_name": "ai-foundry-eus2-01",
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-ai-foundry",
            "uncached_input": 15_000_000,
            "cached_input": 0,
            "cache_write": 0,
            "output": 0,
        },
    ]

    results: List[PeriodUsage] = []

    for p_key in ("today", "week", "month"):
        w = windows[p_key]
        mult = scales[p_key]

        period = PeriodUsage(
            period_name=w.name,
            start_time=w.start,
            end_time=w.end,
            timezone_name=w.timezone_name,
            cost_management_available=(p_key in ("week", "month")),
            cost_management_notes=(
                "Reconciled with Azure Cost Management (sample billed data)"
                if p_key in ("week", "month")
                else "Real-time estimated costs via Azure Monitor metrics (Cost Management pending 4-24hr lag)"
            ),
        )

        for bm in base_models:
            u_in = int(bm["uncached_input"] * mult)
            c_in = int(bm["cached_input"] * mult)
            c_wr = int(bm["cache_write"] * mult)
            out = int(bm["output"] * mult)

            tokens = TokenBreakdown(
                uncached_input=u_in,
                cached_input=c_in,
                cache_write=c_wr,
                output=out,
            )

            uncached_c, cached_c, write_c, out_c, savings = pricing.calculate_cost(
                model_name=bm["model_name"],
                uncached_input=u_in,
                cached_input=c_in,
                output=out,
                cache_write=c_wr,
            )

            # In demo, simulated billed cost slightly matches or has minor variation
            billed = None
            if p_key in ("week", "month"):
                billed = round((uncached_c + cached_c + write_c + out_c) * 0.98, 2)

            cost_obj = CostBreakdown(
                uncached_input_cost=uncached_c,
                cached_input_cost=cached_c,
                cache_write_cost=write_c,
                output_cost=out_c,
                billed_cost=billed,
                currency="USD",
            )

            model_key = f"{bm['resource_name']}/{bm['deployment_name']}"
            period.models[model_key] = ModelUsage(
                model_name=bm["model_name"],
                deployment_name=bm["deployment_name"],
                resource_name=bm["resource_name"],
                subscription_id=bm["subscription_id"],
                resource_group=bm["resource_group"],
                tokens=tokens,
                cost=cost_obj,
                cache_savings=savings,
            )

        period.recalculate_totals()
        results.append(period)

    return results
