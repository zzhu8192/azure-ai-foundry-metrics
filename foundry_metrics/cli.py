"""Command-line interface for Azure AI Foundry Metrics."""

import sys
from typing import Optional
import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from foundry_metrics import __version__
from foundry_metrics.aggregator import MetricsAggregator
from foundry_metrics.auth import AzureAuthManager, get_auth_troubleshooting_guide
from foundry_metrics.discovery import ResourceDiscovery
from foundry_metrics.mock_data import get_demo_periods
from foundry_metrics.pricing import DEFAULT_RATES, PricingManager
from foundry_metrics.views import format_currency, render_dashboard

import shutil

# Auto-load .env file if present
load_dotenv()
_term_w = shutil.get_terminal_size().columns
_target_w = max(_term_w, 115) if _term_w < 115 else _term_w
console = Console(width=_target_w, height=40)


@click.group(invoke_without_command=True)
@click.option(
    "--period",
    "-t",
    type=click.Choice(["all", "today", "week", "month"], case_sensitive=False),
    default="all",
    help="Time period to report (all, today, week, or month).",
)
@click.option(
    "--subscription-id",
    "-s",
    envvar="AZURE_SUBSCRIPTION_ID",
    help="Target Azure Subscription ID. Defaults to all accessible subscriptions or AZURE_SUBSCRIPTION_ID.",
)
@click.option(
    "--resource-group",
    "-g",
    help="Filter by specific Azure Resource Group name.",
)
@click.option(
    "--resource-name",
    "-r",
    help="Filter by specific Azure AI Foundry / OpenAI resource name.",
)
@click.option(
    "--timezone",
    "-tz",
    type=click.Choice(["local", "utc"], case_sensitive=False),
    default="local",
    help="Timezone for day/week/month cutoffs (default: local).",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["table", "summary", "json", "csv"], case_sensitive=False),
    default="table",
    help="Output presentation format.",
)
@click.option(
    "--pricing-file",
    "-p",
    type=click.Path(exists=True),
    help="Custom JSON/YAML file with pricing overrides.",
)
@click.option(
    "--no-cost-management",
    is_flag=True,
    help="Disable querying Azure Cost Management API and use Azure Monitor rates only.",
)
@click.option(
    "--device-code",
    is_flag=True,
    help="Use interactive device code login for Azure authentication.",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Run in demo mode with sample models and telemetry (no Azure login needed).",
)
@click.version_option(version=__version__, prog_name="azure-ai-metrics")
@click.pass_context
def main(
    ctx: click.Context,
    period: str,
    subscription_id: Optional[str],
    resource_group: Optional[str],
    resource_name: Optional[str],
    timezone: str,
    format: str,
    pricing_file: Optional[str],
    no_cost_management: bool,
    device_code: bool,
    demo: bool,
) -> None:
    """Azure AI Foundry Metrics CLI.

    Shows today, current week, and current month credit and token consumption broken down
    by model (uncached vs cached input, output tokens) and Azure costs.
    """
    if ctx.invoked_subcommand is not None:
        return

    # 1. Demo Mode
    if demo:
        periods = get_demo_periods(timezone_name=timezone)
        if period.lower() != "all":
            periods = [p for p in periods if p.period_name.lower().replace("current ", "") == period.lower()]
        render_dashboard(periods, console, output_format=format)
        return

    # 2. Azure Authentication
    auth_mgr = AzureAuthManager(use_device_code=device_code)
    try:
        cred = auth_mgr.get_credential()
        # Fast auth check
        auth_mgr.get_token()
    except Exception as exc:
        console.print(get_auth_troubleshooting_guide())
        console.print(f"[dim]Technical error detail: {exc}[/dim]\n")
        sys.exit(1)

    # 3. Setup Pricing and Aggregator
    pricing_mgr = PricingManager.from_file(pricing_file)
    aggregator = MetricsAggregator(
        credential=cred,
        pricing_manager=pricing_mgr,
        enable_cost_management=not no_cost_management,
    )

    periods_to_run = ["today", "week", "month"] if period.lower() == "all" else [period.lower()]

    with console.status("[bold cyan]Scanning Azure AI Foundry resources and querying metrics...[/bold cyan]"):
        try:
            sub_list = [subscription_id] if subscription_id else None
            periods_data = aggregator.collect_all_periods(
                subscription_ids=sub_list,
                target_rg=resource_group,
                target_name=resource_name,
                timezone_name=timezone,
                periods_to_run=periods_to_run,
            )
        except Exception as exc:
            console.print(f"[bold red]Error querying Azure metrics:[/bold red] {exc}")
            sys.exit(1)

    if not periods_data or not any(p.models for p in periods_data):
        console.print(
            Panel(
                "[bold yellow]No active AI Foundry or Azure OpenAI resources with metrics found.[/bold yellow]\n\n"
                "• Verify your Azure subscription has OpenAI or AIServices resources.\n"
                "• Check that models have received API traffic within the requested time range.\n"
                "• To preview output with sample telemetry, try: [green]azure-ai-metrics --demo[/green]",
                border_style="yellow",
            )
        )
        return

    render_dashboard(periods_data, console, output_format=format)


@main.command(name="auth")
@click.option("--device-code", is_flag=True, help="Use interactive device code login.")
def auth_cmd(device_code: bool) -> None:
    """Verify and diagnose Azure authentication credentials."""
    console.print("[cyan]Verifying Azure credentials...[/cyan]")
    auth_mgr = AzureAuthManager(use_device_code=device_code)
    valid, err = auth_mgr.verify_auth()

    if valid:
        console.print("[bold green]✓ Azure Authentication Successful![/bold green]")
        try:
            discovery = ResourceDiscovery(auth_mgr.get_credential())
            subs = discovery.get_subscriptions()
            console.print(f"[green]Found {len(subs)} accessible Azure subscription(s):[/green]")
            for s in subs:
                console.print(f"  • [cyan]{s}[/cyan]")
        except Exception as e:
            console.print(f"[yellow]Authenticated, but error listing subscriptions: {e}[/yellow]")
    else:
        console.print("[bold red]✗ Azure Authentication Failed[/bold red]")
        console.print(get_auth_troubleshooting_guide())
        if err:
            console.print(f"[dim]Detail: {err}[/dim]")


@main.command(name="rates")
def rates_cmd() -> None:
    """Display the built-in model pricing rate card (per 1M tokens)."""
    table = Table(
        title="Azure AI Foundry & OpenAI Model Rate Card (per 1M tokens USD)",
        header_style="bold cyan",
    )
    table.add_column("Model Pattern", style="bold white")
    table.add_column("Uncached Input", justify="right")
    table.add_column("Cached Input", justify="right", style="green")
    table.add_column("Cache Write", justify="right")
    table.add_column("Output Tokens", justify="right", style="yellow")

    for model, rate in sorted(DEFAULT_RATES.items()):
        table.add_row(
            model,
            format_currency(rate.input_uncached),
            format_currency(rate.input_cached),
            format_currency(rate.cache_write) if rate.cache_write > 0 else "-",
            format_currency(rate.output),
        )

    console.print(table)


@main.command(name="resources")
@click.option("--subscription-id", "-s", envvar="AZURE_SUBSCRIPTION_ID")
@click.option("--device-code", is_flag=True)
def resources_cmd(subscription_id: Optional[str], device_code: bool) -> None:
    """List discovered Azure AI Foundry resources and model deployments."""
    auth_mgr = AzureAuthManager(use_device_code=device_code)
    cred = auth_mgr.get_credential()
    discovery = ResourceDiscovery(cred)

    with console.status("[cyan]Discovering AI Foundry resources...[/cyan]"):
        try:
            subs = [subscription_id] if subscription_id else None
            resources = discovery.discover_resources(subscription_ids=subs)
        except Exception as e:
            console.print(f"[bold red]Discovery error:[/bold red] {e}")
            return

    if not resources:
        console.print("[yellow]No AI Foundry or Azure OpenAI resources found.[/yellow]")
        return

    table = Table(title=f"Discovered Resources ({len(resources)})", header_style="bold cyan")
    table.add_column("Resource Name", style="bold white")
    table.add_column("Resource Group", style="dim")
    table.add_column("Location")
    table.add_column("Kind")
    table.add_column("Deployments", justify="right", style="green")

    for r in resources:
        table.add_row(
            r.name,
            r.resource_group,
            r.location,
            r.kind,
            str(len(r.deployments)),
        )

    console.print(table)


if __name__ == "__main__":
    main()
