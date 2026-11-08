"""Visual formatters for displaying usage metrics and costs (Rich tables, JSON, CSV, Summary)."""

import csv
import io
import json
from typing import List, Optional
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from foundry_metrics.models import PeriodUsage


def format_cost_source(source: str) -> str:
    return {"foundry_metric": "Foundry", "rate_card": "Rate card", "mixed": "Mixed"}.get(source, source)


def format_tokens(num: int, compact: bool = False) -> str:
    """Format large token counts with commas or compact notation (B/M/K)."""
    if not compact:
        return f"{num:,}"
    if abs(num) >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    if abs(num) >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    if abs(num) >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def format_currency(amount: float, currency: str = "USD") -> str:
    """Format dollar currency."""
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{prefix}{amount:,.4f}" if amount < 1.0 and amount > 0 else f"{prefix}{amount:,.2f}"


def render_dashboard(
    periods: List[PeriodUsage],
    console: Console,
    output_format: str = "table",
    compact: Optional[bool] = None,
) -> None:
    """Render metrics dashboard in requested format."""
    if output_format == "json":
        render_json(periods, console)
    elif output_format == "csv":
        render_csv(periods, console)
    elif output_format == "summary":
        render_side_by_side_summary(periods, console, compact=compact)
    else:
        render_tables(periods, console, compact=compact)


def render_tables(
    periods: List[PeriodUsage],
    console: Console,
    compact: Optional[bool] = None,
) -> None:
    """Render detailed Rich tables for each period."""
    if compact is None:
        # Automatically use compact numbers if terminal is under 120 columns
        compact = console.width < 120

    console.print()
    console.print(
        Panel(
            "[bold cyan]Azure AI Foundry[/bold cyan] - [bold white]Credits, Token & Cost Usage Dashboard[/bold white]\n"
            "[dim]Tracking Today, Current Week, and Current Month credits, token breakdowns (cached vs uncached), and model costs[/dim]",
            border_style="cyan",
            expand=False,
        )
    )

    for period in periods:
        _render_single_period_table(period, console, compact=compact)

    if len(periods) > 1:
        render_side_by_side_summary(periods, console, compact=compact)


def _render_single_period_table(
    period: PeriodUsage,
    console: Console,
    compact: bool = False,
) -> None:
    """Render a single period table with per-model breakdown and KPI totals."""
    start_str = period.start_time.strftime("%b %d %H:%M")
    end_str = period.end_time.strftime("%b %d %H:%M")
    time_info = f"{start_str} - {end_str} ({period.timezone_name})"

    table = Table(
        title=f"\n[bold green]{period.period_name.upper()}[/bold green] [dim]({time_info})[/dim]",
        box=box.ROUNDED,
        show_footer=True,
        header_style="bold cyan",
        title_justify="left",
    )

    table.add_column(
        "Model / Deployment",
        style="bold white",
        min_width=18,
        footer="[bold yellow]TOTAL[/bold yellow]",
    )
    table.add_column(
        "Uncached In",
        justify="right",
        no_wrap=True,
        footer=format_tokens(period.total_tokens.uncached_input, compact=compact),
    )
    table.add_column(
        "Cached (Hit %)",
        justify="right",
        no_wrap=True,
        style="green",
        footer=f"{format_tokens(period.total_tokens.cached_input, compact=compact)} ({period.total_tokens.cache_hit_rate:.1f}%)",
    )
    table.add_column(
        "Total In",
        justify="right",
        no_wrap=True,
        footer=format_tokens(period.total_tokens.total_input, compact=compact),
    )
    table.add_column(
        "Output",
        justify="right",
        no_wrap=True,
        footer=format_tokens(period.total_tokens.output, compact=compact),
    )
    table.add_column(
        "Total Tokens",
        justify="right",
        no_wrap=True,
        style="bold magenta",
        footer=format_tokens(period.total_tokens.total_tokens, compact=compact),
    )
    table.add_column(
        "Est. Cost",
        justify="right",
        no_wrap=True,
        style="bold yellow",
        footer=format_currency(period.total_cost.estimated_total_cost),
    )
    table.add_column("Cost Source", footer=format_cost_source(period.total_cost.estimate_source))

    has_billed = period.total_cost.billed_cost is not None
    if has_billed:
        billed_tot = period.total_cost.billed_cost or 0.0
        table.add_column(
            "Billed Cost",
            justify="right",
            no_wrap=True,
            style="bold green",
            footer=format_currency(billed_tot),
        )

    # Sort models by total tokens descending
    sorted_models = sorted(
        period.models.values(),
        key=lambda m: m.tokens.total_tokens,
        reverse=True,
    )

    for m in sorted_models:
        hit_rate_str = f"{m.tokens.cache_hit_rate:.1f}%"
        c_tok_str = format_tokens(m.tokens.cached_input, compact=compact)
        cached_cell = f"{c_tok_str} ({hit_rate_str})" if m.tokens.cached_input > 0 else "0"

        # Format model description
        if m.model_name != m.deployment_name:
            display_name = f"{m.model_name}\n[dim]{m.deployment_name} ({m.resource_name})[/dim]"
        else:
            display_name = f"{m.model_name}\n[dim]({m.resource_name})[/dim]"

        row = [
            display_name,
            format_tokens(m.tokens.uncached_input, compact=compact),
            cached_cell,
            format_tokens(m.tokens.total_input, compact=compact),
            format_tokens(m.tokens.output, compact=compact),
            format_tokens(m.tokens.total_tokens, compact=compact),
            format_currency(m.cost.estimated_total_cost),
            format_cost_source(m.cost.estimate_source),
        ]
        if has_billed:
            b_str = format_currency(m.cost.billed_cost) if m.cost.billed_cost is not None else "-"
            row.append(b_str)

        table.add_row(*row)

    console.print(table)

    # Highlight summary panel
    savings_str = format_currency(period.total_cache_savings)
    savings_text = f"[bold green]{savings_str}[/bold green] saved via prompt caching ({period.total_tokens.cache_hit_rate:.1f}% hit rate)"
    notes = [f"Estimate source: {format_cost_source(period.total_cost.estimate_source)}.", *period.estimate_notes]
    if period.unattributed_billed_cost:
        notes.append(f"Billed total includes {format_currency(period.unattributed_billed_cost)} that cannot be attributed to a deployment.")
    if period.cost_management_notes:
        notes.append(period.cost_management_notes)
    note_text = "\n[dim]" + "\n".join(notes) + "[/dim]"

    summary_panel = Panel(
        f"[bold]Estimated Azure Cost:[/bold] [bold yellow]{format_currency(period.total_cost.estimated_total_cost)}[/bold yellow]  |  "
        f"[bold]Est. Cache Savings:[/bold] {savings_text}  |  "
        f"[bold]Total Tokens:[/bold] [bold magenta]{format_tokens(period.total_tokens.total_tokens, compact=False)}[/bold magenta]"
        f"{note_text}",
        border_style="green" if period.total_cache_savings > 0 else "yellow",
        expand=False,
    )
    console.print(summary_panel)


def render_side_by_side_summary(
    periods: List[PeriodUsage],
    console: Console,
    compact: Optional[bool] = None,
) -> None:
    """Render a compact comparison table comparing Today, Current Week, and Current Month."""
    if compact is None:
        compact = console.width < 120

    table = Table(
        title="\n[bold cyan]SUMMARY COMPARISON - Azure AI Foundry Credits & Tokens[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
    )

    table.add_column("Metric", style="bold white")
    for p in periods:
        table.add_column(f"{p.period_name}", justify="right", no_wrap=True)

    # Rows
    table.add_row(
        "Uncached Input Tokens",
        *[format_tokens(p.total_tokens.uncached_input, compact=compact) for p in periods],
    )
    table.add_row(
        "Cached Input Tokens",
        *[f"{format_tokens(p.total_tokens.cached_input, compact=compact)} ({p.total_tokens.cache_hit_rate:.1f}%)" for p in periods],
        style="green",
    )
    table.add_row(
        "Total Input Tokens",
        *[format_tokens(p.total_tokens.total_input, compact=compact) for p in periods],
    )
    table.add_row(
        "Output Tokens",
        *[format_tokens(p.total_tokens.output, compact=compact) for p in periods],
    )
    table.add_row(
        "Total Tokens",
        *[format_tokens(p.total_tokens.total_tokens, compact=compact) for p in periods],
        style="bold magenta",
    )
    table.add_section()
    table.add_row(
        "Est. Azure Cost",
        *[format_currency(p.total_cost.estimated_total_cost) for p in periods],
        style="bold yellow",
    )
    table.add_row("Estimate Source", *[format_cost_source(p.total_cost.estimate_source) for p in periods])

    has_any_billed = any(p.total_cost.billed_cost is not None for p in periods)
    if has_any_billed:
        table.add_row(
            "Billed Cost (Cost Mgmt)",
            *[format_currency(p.total_cost.billed_cost) if p.total_cost.billed_cost is not None else "[dim]Pending[/dim]" for p in periods],
            style="bold green",
        )

    table.add_row(
        "Prompt Cache Savings",
        *[format_currency(p.total_cache_savings) for p in periods],
        style="bold green",
    )

    console.print(table)


def render_json(periods: List[PeriodUsage], console: Console) -> None:
    """Render JSON formatted output for script pipelines."""
    out = []
    for p in periods:
        p_dict = {
            "period": p.period_name,
            "start_time": p.start_time.isoformat(),
            "end_time": p.end_time.isoformat(),
            "timezone": p.timezone_name,
            "total_tokens": {
                "uncached_input": p.total_tokens.uncached_input,
                "cached_input": p.total_tokens.cached_input,
                "cache_write": p.total_tokens.cache_write,
                "total_input": p.total_tokens.total_input,
                "cache_hit_rate_pct": round(p.total_tokens.cache_hit_rate, 2),
                "output": p.total_tokens.output,
                "total": p.total_tokens.total_tokens,
            },
            "total_cost": {
                "estimated_usd": round(p.total_cost.estimated_total_cost, 4),
                "billed_usd": round(p.total_cost.billed_cost, 4) if p.total_cost.billed_cost is not None else None,
                "effective_usd": round(p.total_cost.effective_cost, 4),
                "cache_savings_usd": round(p.total_cache_savings, 4),
                "source": p.total_cost.estimate_source,
                "foundry_estimated_usd": p.total_cost.foundry_estimated_cost,
                "rate_card_estimated_usd": round(p.total_cost.rate_card_total_cost, 4),
                "unattributed_billed_usd": round(p.unattributed_billed_cost, 4),
            },
            "estimate_notes": p.estimate_notes,
            "billing_notes": p.cost_management_notes,
            "models": {},
        }
        for k, m in p.models.items():
            p_dict["models"][k] = {
                "model_name": m.model_name,
                "deployment_name": m.deployment_name,
                "resource_name": m.resource_name,
                "subscription_id": m.subscription_id,
                "tokens": {
                    "uncached_input": m.tokens.uncached_input,
                    "cached_input": m.tokens.cached_input,
                    "cache_write": m.tokens.cache_write,
                    "total_input": m.tokens.total_input,
                    "cache_hit_rate_pct": round(m.tokens.cache_hit_rate, 2),
                    "output": m.tokens.output,
                    "total": m.tokens.total_tokens,
                },
                "cost": {
                    "estimated_usd": round(m.cost.estimated_total_cost, 4),
                    "billed_usd": round(m.cost.billed_cost, 4) if m.cost.billed_cost is not None else None,
                    "effective_usd": round(m.cost.effective_cost, 4),
                    "cache_savings_usd": round(m.cache_savings, 4),
                    "source": m.cost.estimate_source,
                    "foundry_estimated_usd": m.cost.foundry_estimated_cost,
                    "rate_card_estimated_usd": round(m.cost.rate_card_total_cost, 4),
                },
            }
        out.append(p_dict)

    console.print_json(data=out)


def render_csv(periods: List[PeriodUsage], console: Console) -> None:
    """Render CSV formatted output."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Period",
        "Resource",
        "Deployment",
        "Model",
        "UncachedInputTokens",
        "CachedInputTokens",
        "TotalInputTokens",
        "CacheHitRatePct",
        "OutputTokens",
        "TotalTokens",
        "EstimatedCostUSD",
        "BilledCostUSD",
        "CacheSavingsUSD",
        "CostSource",
        "FoundryEstimatedCostUSD",
        "RateCardEstimatedCostUSD",
    ])

    for p in periods:
        for m in p.models.values():
            writer.writerow([
                p.period_name,
                m.resource_name,
                m.deployment_name,
                m.model_name,
                m.tokens.uncached_input,
                m.tokens.cached_input,
                m.tokens.total_input,
                f"{m.tokens.cache_hit_rate:.2f}",
                m.tokens.output,
                m.tokens.total_tokens,
                f"{m.cost.estimated_total_cost:.4f}",
                f"{m.cost.billed_cost:.4f}" if m.cost.billed_cost is not None else "",
                f"{m.cache_savings:.4f}",
                m.cost.estimate_source,
                f"{m.cost.foundry_estimated_cost:.4f}" if m.cost.foundry_estimated_cost is not None else "",
                f"{m.cost.rate_card_total_cost:.4f}",
            ])
        # Period total row
        writer.writerow([
            f"{p.period_name} - TOTAL",
            "ALL",
            "ALL",
            "ALL",
            p.total_tokens.uncached_input,
            p.total_tokens.cached_input,
            p.total_tokens.total_input,
            f"{p.total_tokens.cache_hit_rate:.2f}",
            p.total_tokens.output,
            p.total_tokens.total_tokens,
            f"{p.total_cost.estimated_total_cost:.4f}",
            f"{p.total_cost.billed_cost:.4f}" if p.total_cost.billed_cost is not None else "",
            f"{p.total_cache_savings:.4f}",
            p.total_cost.estimate_source,
            f"{p.total_cost.foundry_estimated_cost:.4f}" if p.total_cost.foundry_estimated_cost is not None else "",
            f"{p.total_cost.rate_card_total_cost:.4f}",
        ])

    console.print(output.getvalue(), end="", markup=False, highlight=False, soft_wrap=True)
