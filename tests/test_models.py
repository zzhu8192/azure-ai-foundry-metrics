"""Unit tests for data models and aggregations."""

from datetime import datetime, timezone
from foundry_metrics.models import (
    CostBreakdown,
    ModelUsage,
    PeriodUsage,
    TokenBreakdown,
)


def test_token_breakdown():
    tb = TokenBreakdown(
        uncached_input=1000,
        cached_input=3000,
        output=500,
    )
    assert tb.total_input == 4000
    assert tb.total_tokens == 4500
    assert tb.cache_hit_rate == 75.0


def test_token_breakdown_add():
    tb1 = TokenBreakdown(uncached_input=100, cached_input=200, output=50)
    tb2 = TokenBreakdown(uncached_input=300, cached_input=400, output=150)
    res = tb1.add(tb2)
    assert res.uncached_input == 400
    assert res.cached_input == 600
    assert res.total_input == 1000
    assert res.output == 200
    assert res.total_tokens == 1200
    assert res.cache_hit_rate == 60.0


def test_period_usage_recalculate():
    now = datetime.now(timezone.utc)
    pu = PeriodUsage(
        period_name="Today",
        start_time=now,
        end_time=now,
        timezone_name="UTC",
    )

    m1 = ModelUsage(
        model_name="gpt-4o",
        deployment_name="gpt-4o",
        resource_name="res1",
        subscription_id="sub1",
        resource_group="rg1",
        tokens=TokenBreakdown(uncached_input=100, cached_input=100, output=50),
        cost=CostBreakdown(uncached_input_cost=1.0, cached_input_cost=0.5, output_cost=2.0),
        cache_savings=0.5,
    )
    m2 = ModelUsage(
        model_name="gpt-4o-mini",
        deployment_name="gpt-4o-mini",
        resource_name="res1",
        subscription_id="sub1",
        resource_group="rg1",
        tokens=TokenBreakdown(uncached_input=200, cached_input=300, output=100),
        cost=CostBreakdown(uncached_input_cost=0.2, cached_input_cost=0.1, output_cost=0.5),
        cache_savings=0.2,
    )

    pu.models["res1/gpt-4o"] = m1
    pu.models["res1/gpt-4o-mini"] = m2
    pu.recalculate_totals()

    assert pu.total_tokens.uncached_input == 300
    assert pu.total_tokens.cached_input == 400
    assert pu.total_tokens.total_input == 700
    assert pu.total_tokens.output == 150
    assert pu.total_tokens.total_tokens == 850
    assert round(pu.total_cost.estimated_total_cost, 2) == 4.30
    assert round(pu.total_cache_savings, 2) == 0.70
