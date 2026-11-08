"""Pricing rate card and cost calculator for Azure AI Foundry models.

All rates are specified in USD per 1,000,000 (1M) tokens.
"""

import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


@dataclass
class ModelRate:
    """Pricing rates per 1,000,000 tokens in USD."""
    input_uncached: float  # Price per 1M uncached prompt tokens
    input_cached: float    # Price per 1M cached prompt tokens
    output: float          # Price per 1M completion tokens
    cache_write: float = 0.0  # Price per 1M cache write tokens (Anthropic / Claude)
    currency: str = "USD"


# Standard public pricing for Azure AI Foundry & Azure OpenAI
DEFAULT_RATES: Dict[str, ModelRate] = {
    # OpenAI GPT-4o family
    "gpt-4o": ModelRate(input_uncached=2.50, input_cached=1.25, output=10.00),
    "gpt-4o-mini": ModelRate(input_uncached=0.15, input_cached=0.075, output=0.60),
    "gpt-4o-realtime-preview": ModelRate(input_uncached=5.00, input_cached=2.50, output=20.00),
    "gpt-4o-audio-preview": ModelRate(input_uncached=2.50, input_cached=1.25, output=10.00),

    # OpenAI GPT-5 & GPT-6 families (Azure AI Foundry)
    # Global Standard, short context. Portal estimates take precedence over these
    # reference prices; use overrides for other deployment types and agreements.
    "gpt-6.1-sol": ModelRate(input_uncached=2.00, input_cached=0.10, output=10.00, cache_write=2.50),
    "gpt-6-sol": ModelRate(input_uncached=2.00, input_cached=0.20, output=10.00, cache_write=2.50),
    "gpt-6-astra": ModelRate(input_uncached=10.00, input_cached=1.00, output=50.00, cache_write=12.50),
    "gpt-5.6": ModelRate(input_uncached=5.00, input_cached=0.50, output=30.00, cache_write=6.25),
    "gpt-5.6-sol": ModelRate(input_uncached=5.00, input_cached=0.50, output=30.00, cache_write=6.25),
    "gpt-5.6-terra": ModelRate(input_uncached=2.00, input_cached=0.20, output=12.00, cache_write=2.50),
    "gpt-5.6-luna": ModelRate(input_uncached=0.20, input_cached=0.02, output=1.20, cache_write=0.25),
    "gpt-5.4-mini": ModelRate(input_uncached=0.15, input_cached=0.075, output=0.60),
    "gpt-5.3-codex": ModelRate(input_uncached=2.00, input_cached=1.00, output=8.00),
    "gpt-5": ModelRate(input_uncached=2.50, input_cached=1.25, output=10.00),

    # OpenAI Reasoning models
    "o1": ModelRate(input_uncached=15.00, input_cached=7.50, output=60.00),
    "o1-mini": ModelRate(input_uncached=3.00, input_cached=1.50, output=12.00),
    "o1-preview": ModelRate(input_uncached=15.00, input_cached=7.50, output=60.00),
    "o3-mini": ModelRate(input_uncached=1.10, input_cached=0.55, output=4.40),

    # OpenAI Legacy / GPT-4 & 3.5
    "gpt-4-turbo": ModelRate(input_uncached=10.00, input_cached=10.00, output=30.00),
    "gpt-4": ModelRate(input_uncached=30.00, input_cached=30.00, output=60.00),
    "gpt-4-32k": ModelRate(input_uncached=60.00, input_cached=60.00, output=120.00),
    "gpt-35-turbo": ModelRate(input_uncached=0.50, input_cached=0.50, output=1.50),
    "gpt-35-turbo-16k": ModelRate(input_uncached=3.00, input_cached=3.00, output=4.00),

    # Embeddings
    "text-embedding-3-small": ModelRate(input_uncached=0.02, input_cached=0.02, output=0.00),
    "text-embedding-3-large": ModelRate(input_uncached=0.13, input_cached=0.13, output=0.00),
    "text-embedding-ada-002": ModelRate(input_uncached=0.10, input_cached=0.10, output=0.00),

    # Anthropic Claude models on Azure
    "claude-3-5-sonnet": ModelRate(input_uncached=3.00, input_cached=0.30, output=15.00, cache_write=3.75),
    "claude-3-7-sonnet": ModelRate(input_uncached=3.00, input_cached=0.30, output=15.00, cache_write=3.75),
    "claude-3-5-haiku": ModelRate(input_uncached=0.80, input_cached=0.08, output=4.00, cache_write=1.00),
    "claude-3-opus": ModelRate(input_uncached=15.00, input_cached=1.50, output=75.00, cache_write=18.75),

    # DeepSeek models on Azure AI Foundry
    "deepseek-r1": ModelRate(input_uncached=0.55, input_cached=0.14, output=2.19),
    "deepseek-v3": ModelRate(input_uncached=0.14, input_cached=0.035, output=0.28),

    # Meta Llama models on Azure AI Foundry
    "meta-llama-3.1-405b": ModelRate(input_uncached=2.40, input_cached=2.40, output=2.40),
    "meta-llama-3.1-70b": ModelRate(input_uncached=0.70, input_cached=0.70, output=0.80),
    "meta-llama-3.3-70b": ModelRate(input_uncached=0.70, input_cached=0.70, output=0.80),
    "meta-llama-3.1-8b": ModelRate(input_uncached=0.15, input_cached=0.15, output=0.20),

    # Mistral models
    "mistral-large": ModelRate(input_uncached=2.00, input_cached=2.00, output=6.00),
    "mistral-small": ModelRate(input_uncached=0.20, input_cached=0.20, output=0.60),

    # Cohere models
    "cohere-command-r-plus": ModelRate(input_uncached=2.50, input_cached=2.50, output=10.00),
    "cohere-command-r": ModelRate(input_uncached=0.15, input_cached=0.15, output=0.60),
}

# Fallback default rate if model is completely unrecognized
FALLBACK_RATE = ModelRate(input_uncached=1.00, input_cached=0.50, output=2.00)


class PricingManager:
    """Manages model rate resolution, custom pricing overrides, and cost calculation."""

    def __init__(self, custom_rates: Optional[Dict[str, ModelRate]] = None):
        self.rates: Dict[str, ModelRate] = dict(DEFAULT_RATES)
        if custom_rates:
            self.rates.update(custom_rates)
        self._warned_models = set()

    @classmethod
    def from_file(cls, path_or_str: Optional[str] = None) -> "PricingManager":
        """Load pricing with optional overrides from a JSON or YAML file."""
        custom_rates: Dict[str, ModelRate] = {}
        target_path = path_or_str or os.environ.get("FOUNDRY_PRICING_FILE")

        if target_path and Path(target_path).is_file():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, val in data.items():
                        custom_rates[key.lower()] = ModelRate(
                            input_uncached=float(val.get("input_uncached", val.get("input", 0.0))),
                            input_cached=float(val.get("input_cached", val.get("cached", 0.0))),
                            output=float(val.get("output", 0.0)),
                            cache_write=float(val.get("cache_write", 0.0)),
                            currency=val.get("currency", "USD"),
                        )
            except Exception as exc:
                print(f"Warning: Failed to load pricing file '{target_path}': {exc}")

        return cls(custom_rates=custom_rates)

    def resolve_rate(self, model_identifier: str) -> Tuple[str, ModelRate]:
        """Resolve a model name or deployment name to the closest matching rate card.

        Returns (canonical_name, ModelRate).
        """
        raw = model_identifier.lower().strip()
        # Clean common prefixes or suffixes (e.g. example-foundry-gpt-4o-mini -> gpt-4o-mini)
        cleaned = re.sub(r"^[a-z0-9_-]*?(gpt|o1|o3|claude|deepseek|meta-llama|llama|mistral|cohere|text-embedding)", r"\1", raw)

        # 1. Exact match
        if cleaned in self.rates:
            return cleaned, self.rates[cleaned]
        if raw in self.rates:
            return raw, self.rates[raw]

        # 2. Date version strip (e.g. gpt-4o-2024-08-06 -> gpt-4o)
        version_stripped = re.sub(r"-(\d{4}-\d{2}-\d{2}|\d{6}|\d{4}|preview.*|instruct.*)$", "", cleaned)
        if version_stripped in self.rates:
            return version_stripped, self.rates[version_stripped]

        # 3. Substring matching for well-known keys
        for key, rate in sorted(self.rates.items(), key=lambda item: len(item[0]), reverse=True):
            # Match complete model names, allowing deployment decorations and
            # date suffixes without treating gpt-6.1 as gpt-6 (or gpt-5.6 as gpt-5).
            pattern = rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9.])"
            if re.search(pattern, cleaned) or re.search(pattern, raw):
                return key, rate

        if raw not in self._warned_models:
            warnings.warn(
                f"No rate card for {model_identifier!r}; using fallback prices. "
                "Supply --pricing-file for a reliable rate-card estimate.",
                stacklevel=2,
            )
            self._warned_models.add(raw)
        return raw, FALLBACK_RATE

    def calculate_cost(
        self,
        model_name: str,
        uncached_input: int,
        cached_input: int,
        output: int,
        cache_write: int = 0,
    ) -> Tuple[float, float, float, float, float]:
        """Calculate costs in USD for a given token usage.

        Returns:
            (uncached_input_cost, cached_input_cost, cache_write_cost, output_cost, cache_savings)
        """
        _, rate = self.resolve_rate(model_name)

        uncached_cost = (uncached_input / 1_000_000.0) * rate.input_uncached
        cached_cost = (cached_input / 1_000_000.0) * rate.input_cached
        write_cost = (cache_write / 1_000_000.0) * rate.cache_write
        output_cost = (output / 1_000_000.0) * rate.output

        # Cache savings: what this cached usage would have cost at full uncached price vs what it actually cost
        full_price_for_cached = (cached_input / 1_000_000.0) * rate.input_uncached
        cache_savings = max(0.0, full_price_for_cached - cached_cost)

        return uncached_cost, cached_cost, write_cost, output_cost, cache_savings
