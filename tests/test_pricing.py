"""Unit tests for pricing rate resolution and cost calculations."""

from foundry_metrics.models import TokenBreakdown
from foundry_metrics.pricing import PricingManager, ModelRate


def test_pricing_exact_resolution():
    pm = PricingManager()
    name, rate = pm.resolve_rate("gpt-4o")
    assert name == "gpt-4o"
    assert rate.input_uncached == 2.50
    assert rate.input_cached == 1.25
    assert rate.output == 10.00


def test_pricing_fuzzy_version_resolution():
    pm = PricingManager()
    name, rate = pm.resolve_rate("gpt-4o-2024-08-06")
    assert rate.input_uncached == 2.50

    name2, rate2 = pm.resolve_rate("example-foundry-gpt-4o-mini")
    assert rate2.input_uncached == 0.15
    assert rate2.output == 0.60


def test_pricing_cost_calculation():
    pm = PricingManager()
    # 1,000,000 uncached ($2.50) + 1,000,000 cached ($1.25) + 500,000 output ($5.00)
    uncached_c, cached_c, write_c, out_c, savings = pm.calculate_cost(
        model_name="gpt-4o",
        uncached_input=1_000_000,
        cached_input=1_000_000,
        output=500_000,
    )
    assert uncached_c == 2.50
    assert cached_c == 1.25
    assert write_c == 0.0
    assert out_c == 5.00
    # Full price for cached would be $2.50. At $1.25, savings is $1.25
    assert savings == 1.25


def test_pricing_claude_cache_write():
    pm = PricingManager()
    uncached_c, cached_c, write_c, out_c, savings = pm.calculate_cost(
        model_name="claude-3-5-sonnet",
        uncached_input=1_000_000,
        cached_input=1_000_000,
        output=1_000_000,
        cache_write=1_000_000,
    )
    assert uncached_c == 3.00
    assert cached_c == 0.30
    assert write_c == 3.75
    assert out_c == 15.00
    assert savings == 2.70


def test_pricing_gpt_5_6_luna():
    pm = PricingManager()
    name, rate = pm.resolve_rate("gpt-5.6-luna")
    assert name == "gpt-5.6-luna"
    assert rate.input_uncached == 0.20
    assert rate.input_cached == 0.02
    assert rate.output == 1.20

    # Test synthetic token calculation:
    # 600,000 uncached, 50,000 cached, 15,000 output
    uncached_c, cached_c, write_c, out_c, savings = pm.calculate_cost(
        model_name="gpt-5.6-luna",
        uncached_input=600_000,
        cached_input=50_000,
        output=15_000,
    )
    total_est = uncached_c + cached_c + write_c + out_c
    assert round(total_est, 4) == 0.1390
    assert round(savings, 4) == 0.0090

