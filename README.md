# Azure AI Foundry Metrics CLI (`azure-ai-metrics`)

A command-line application and dashboard to monitor your **Current Day**, **Current Week**, and **Current Month** Azure AI Foundry (and Azure OpenAI) credit and token consumption.

Provides a breakdown **per hosted model**:
- **Input Tokens**: Uncached input tokens vs. Cached input tokens (with cache hit percentage)
- **Output Tokens**: Generated completion tokens
- **Total Tokens**: Full token volume
- **Azure Cost**: Foundry's estimated model cost when available, with rate-card fallback and separate delayed Azure Cost Management charges

---

## Features

- **Multi-Period Telemetry**: View **Today** (midnight to now), **Current Week** (Monday to now), and **Current Month** (1st of month to now).
- **Prompt Caching Visibility**: Breakdown of cached vs uncached input tokens, approximate cache match rates, and estimated savings from reference prices.
- **Hybrid Real-Time Cost Engine**:
  - **Foundry Estimate**: Uses Azure Monitor's `FoundryModelEstimatedCost` (Sum) for each deployment when available. The Cost Source column identifies Foundry, Rate card, or a Mixed total.
  - **Rate-Card Fallback**: Uses reference short-context list prices and approximate cache counts when the cost metric has no data. Override prices for other regions, deployment types, long contexts, or contracted rates. OpenAI cache-write telemetry may be unavailable, so the fallback can differ from Foundry.
  - **Separate Billing**: Queries Azure Cost Management for delayed, full UTC-day charges. Billing never replaces live token totals or Foundry estimates. Model-level meters are assigned only when there is one deployment of that model; shared-model and unmatched charges remain unattributed.
- **Broad Model Support**: Built-in pricing for GPT-4o, GPT-4o-mini, o1, o1-mini, o3-mini, GPT-4 Turbo, GPT-3.5, Claude 3.5/3.7 Sonnet, Claude 3.5 Haiku, DeepSeek R1/V3, Llama 3.1/3.3, Mistral, and Cohere.
- **Flexible Authentication**: Supports Azure Default Credential (`AZURE_CLIENT_ID`/`SECRET`/`TENANT_ID`), Azure CLI (`az login`), or interactive device code login (`--device-code`).
- **Multiple Output Formats**: Rich color terminal tables, side-by-side comparison summary, machine-readable JSON, or CSV export.
- **Demo Mode**: Built-in offline preview (`--demo`) with realistic telemetry across multiple models.

---

## Quick Start

### 1. Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first. The project requires Python 3.10 or newer; uv can install Python automatically if needed.

These commands work in PowerShell, Bash, and other shells:

```bash
# Clone or navigate to the repository
cd azure-ai-foundry-metrics

# Install the project and dependencies from uv.lock
uv sync --locked
```

`uv sync` installs the project in editable mode and includes the development dependencies for testing. uv creates and manages `.venv` automatically; no manual creation or activation is needed. Run commands with `uv run` from the repository directory. For an installation without development dependencies, use `uv sync --locked --no-dev` and `uv run --locked --no-dev azure-ai-metrics`.

### 2. Try the Demo (Offline Preview)

To see the dashboard immediately without connecting to Azure:

```bash
uv run --locked azure-ai-metrics --demo
```

Or view today only:

```bash
uv run --locked azure-ai-metrics --demo --period today
```

---

## Azure Authentication

The tool uses Azure's standard identity system. Choose the method that best fits your environment:

### Option A: Service Principal (Environment Variables)
Set these in your terminal or create a `.env` file:

```bash
export AZURE_TENANT_ID="<your-tenant-id>"
export AZURE_CLIENT_ID="<your-client-id>"
export AZURE_CLIENT_SECRET="<your-client-secret>"
export AZURE_SUBSCRIPTION_ID="<your-subscription-id>"  # optional default
```

### Option B: Interactive Device Code Login
If you do not have a Service Principal, run with `--device-code`:

```bash
uv run --locked azure-ai-metrics --device-code
```
This prints a code and URL to authenticate in your browser.

### Option C: Azure CLI
If `az` is installed on your system:

```bash
az login
```

### Verify Authentication
Run the `auth` diagnostic command:

```bash
uv run --locked azure-ai-metrics auth
```

---

## CLI Usage

```text
Usage: azure-ai-metrics [OPTIONS] [COMMAND] [ARGS]...

Options:
  -t, --period [all|today|week|month|7d]
                                  Time period to report (default: all).
  -s, --subscription-id TEXT      Target Azure Subscription ID.
  -g, --resource-group TEXT       Filter by specific Resource Group.
  -r, --resource-name TEXT        Filter by specific AI Foundry / OpenAI resource.
  -tz, --timezone TEXT           local, utc, or an IANA zone (default: local).
  --deployment TEXT             Exact deployment name to report.
  --start TEXT                  Custom range start, ISO date/time.
  --end TEXT                    Exclusive custom end, ISO date/time (default: now).
  -f, --format [table|summary|json|csv]
                                  Output presentation format (default: table).
  -p, --pricing-file PATH         Custom JSON/YAML file with pricing overrides.
  --no-cost-management            Use real-time monitor rates only (skip Cost Mgmt API).
  --device-code                   Use interactive device code login.
  --demo                          Run in demo mode with sample models.
  --help                          Show this message and exit.

Commands:
  auth       Verify and diagnose Azure authentication credentials.
  rates      Display the built-in model pricing rate card.
  resources  List discovered Azure AI Foundry resources and deployments.
```

---

## Example Commands

### Compare with a Foundry Deployment's 7D Monitor View

```bash
uv run azure-ai-metrics --period 7d --timezone America/Chicago --resource-name example-ai-foundry --deployment gpt-6.1-sol
```

`7d` covers exactly the last 168 hours; `week` remains Monday-to-now. Select the same resource, deployment, and start/end instants in both views. Local and IANA timezones handle daylight saving changes.

For a fixed comparison, use `--start` and optionally `--end`; these select a custom range instead of the calendar periods. A date alone means midnight in the selected timezone, and the end is exclusive. This example includes the full local days October 31 through November 7:

```bash
uv run azure-ai-metrics --timezone America/Chicago --start 2026-10-31 --end 2026-11-08 --deployment gpt-6.1-sol
```

Foundry estimates are not invoices. Token and cost ingestion can lag independently. JSON and CSV include the estimate source, Foundry estimate, and rate-card estimate so differences remain visible. JSON also includes billing and estimate notes. See [Azure metric definitions](https://learn.microsoft.com/en-us/azure/foundry/openai/monitor-openai-reference) and [Foundry cost guidance](https://learn.microsoft.com/en-us/azure/foundry/concepts/manage-costs).

In JSON, `effective_usd` is a compatibility alias for the displayed estimate (`estimated_usd`). Delayed actual charges are available separately as `billed_usd`.

### Full Dashboard (Today, Week, Month + Comparison Summary)
```bash
uv run --locked azure-ai-metrics
```

### View Today Only
```bash
uv run --locked azure-ai-metrics --period today
```

### View Compact Side-by-Side Summary
```bash
uv run --locked azure-ai-metrics --format summary
```

### Export to JSON (for Scripting or Piping to `jq`)
```bash
uv run --locked azure-ai-metrics --period today --format json | jq '.[] | .total_cost'
```

### Export to CSV
```bash
uv run --locked azure-ai-metrics --format csv > usage_report.csv
```

### Inspect Built-in Rate Card
```bash
uv run --locked azure-ai-metrics rates
```

### List Discovered AI Foundry Resources
```bash
uv run --locked azure-ai-metrics resources
```

---

## Custom Pricing Overrides

If your organization has custom enterprise discounted rates (e.g. EA, MCA, or PTU commitments), create a JSON file (e.g., `pricing.custom.json`):

```json
{
  "gpt-4o": {
    "input_uncached": 2.00,
    "input_cached": 1.00,
    "output": 8.00
  }
}
```

Then supply it via CLI:

```bash
uv run --locked azure-ai-metrics --pricing-file pricing.custom.json
```

Or via environment variable:

```bash
export FOUNDRY_PRICING_FILE=pricing.custom.json
```

---

## Architecture & How It Works

1. **Discovery**: Queries Azure Resource Manager to locate all `Microsoft.CognitiveServices/accounts` (OpenAI, AIServices) and Azure AI Foundry Hubs/Projects across your subscription, along with model deployments.
2. **Azure Monitor Metrics**: Discovers available metrics and queries token counts and Foundry cost with Sum, cache match rates with Average, split by deployment. Unsupported metrics are isolated rather than failing the entire query.
3. **Prompt Cache Calculation**:
   - For OpenAI deployments: calculates cached tokens from the cache match rate (`total_prompt * match_rate%`), separating uncached and cached input tokens.
   - For Anthropic / Claude on Azure: queries `cacheReadInputTokens` directly.
4. **Cost Evaluation**: Prefers Foundry's reported estimate; applies model rate cards only as a fallback. GPT-6.1 Sol has its own entry. Matching respects model-version boundaries rather than treating newer models as generic GPT-6. Cache savings are rate-card estimates.
5. **Cost Management**: Queries daily charges for a separate billed-cost column without replacing live usage. These records can cover more time than a local or partial-day Monitor range.

---

## Running Unit & Integration Tests

```bash
uv sync --locked
uv run --locked pytest tests/
```

Dependencies are defined in `pyproject.toml` and pinned in `uv.lock`. After changing dependencies, run `uv lock` and commit both files.
