# Comparing token counts with Foundry

Use the same resource, deployment, start instant, and end instant in both views.
`--period 7d` is a rolling 168-hour window. The CLI prints its bounds; use
`--start` and `--end` to repeat a comparison with fixed bounds.

The CLI prefers Azure Monitor's `TotalTokens` (Sum) for total token counts.
When that metric has no reported samples, it falls back to input plus output.
Input and output prefer `InputTokens` and `OutputTokens`; the legacy
`ProcessedPromptTokens` and `GeneratedTokens` are fallback sources. Aliases
are never added together.

These metrics are ingested separately. If the reported total differs from input
plus output, the CLI preserves both and displays a note. It does not infer that
the difference is cache tokens or reasoning tokens, and does not invent input
or output charges for that difference.

To inspect the exact values and time bounds without daily billing queries:

```powershell
uv run azure-ai-metrics --period 7d --timezone America/Chicago --deployment gpt-6.1-sol --no-cost-management --format json > metrics.json
```

For each model, `metric_totals` contains the totals returned by Azure Monitor,
including canonical and legacy token metrics. `tokens.source` identifies
`TotalTokens` or `input_plus_output`. `tokens.input_plus_output` and
`tokens.reported_total_difference` expose the component sum and signed difference.
Period totals use the same fields; the source is `mixed` when deployments use
different sources. The `FoundryModelEstimatedCost` entry is USD, not a token count.

Caching affects input pricing, not the total token count. Cache reads and writes
are subdivisions of input tokens; reasoning tokens are a subdivision of output.
Do not add those subdivisions a second time. Missing telemetry remains missing.

See [Microsoft's metric reference](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/monitor-models)
for definitions of `InputTokens`, `OutputTokens`, and `TotalTokens`.
