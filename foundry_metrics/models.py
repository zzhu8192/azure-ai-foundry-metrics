"""Data models for Azure AI Foundry metrics and costs."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class TokenBreakdown:
    """Breakdown of tokens for a model or aggregated total."""
    uncached_input: int = 0
    cached_input: int = 0
    cache_write: int = 0
    output: int = 0

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cached_input + self.cache_write

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.output

    @property
    def cache_hit_rate(self) -> float:
        """Percentage of input tokens served from cache."""
        if self.total_input <= 0:
            return 0.0
        return (self.cached_input / self.total_input) * 100.0

    def add(self, other: "TokenBreakdown") -> "TokenBreakdown":
        return TokenBreakdown(
            uncached_input=self.uncached_input + other.uncached_input,
            cached_input=self.cached_input + other.cached_input,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
        )


@dataclass
class CostBreakdown:
    """Cost breakdown for a model or aggregated total."""
    uncached_input_cost: float = 0.0
    cached_input_cost: float = 0.0
    cache_write_cost: float = 0.0
    output_cost: float = 0.0
    billed_cost: Optional[float] = None
    currency: str = "USD"
    foundry_estimated_cost: Optional[float] = None
    estimate_source: str = "rate_card"
    # Aggregated totals can combine portal estimates and rate-card fallbacks.
    combined_estimated_cost: Optional[float] = None

    @property
    def estimated_total_cost(self) -> float:
        if self.combined_estimated_cost is not None:
            return self.combined_estimated_cost
        if self.foundry_estimated_cost is not None:
            return self.foundry_estimated_cost
        return self.rate_card_total_cost

    @property
    def rate_card_total_cost(self) -> float:
        return (
            self.uncached_input_cost
            + self.cached_input_cost
            + self.cache_write_cost
            + self.output_cost
        )

    @property
    def effective_cost(self) -> float:
        """Compatibility alias for the displayed estimate; billing is separate."""
        return self.estimated_total_cost

    def add(self, other: "CostBreakdown") -> "CostBreakdown":
        billed = None
        if self.billed_cost is not None or other.billed_cost is not None:
            billed = (self.billed_cost or 0.0) + (other.billed_cost or 0.0)

        return CostBreakdown(
            uncached_input_cost=self.uncached_input_cost + other.uncached_input_cost,
            cached_input_cost=self.cached_input_cost + other.cached_input_cost,
            cache_write_cost=self.cache_write_cost + other.cache_write_cost,
            output_cost=self.output_cost + other.output_cost,
            billed_cost=billed,
            currency=self.currency,
            foundry_estimated_cost=(
                self.foundry_estimated_cost + other.foundry_estimated_cost
                if self.foundry_estimated_cost is not None and other.foundry_estimated_cost is not None
                else None
            ),
            estimate_source=(self.estimate_source if self.estimate_source == other.estimate_source else "mixed"),
            combined_estimated_cost=self.estimated_total_cost + other.estimated_total_cost,
        )


@dataclass
class ModelUsage:
    """Usage statistics for a single model deployment."""
    model_name: str
    deployment_name: str
    resource_name: str
    subscription_id: str
    resource_group: str
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    cache_savings: float = 0.0  # Dollars saved due to prompt caching


@dataclass
class PeriodUsage:
    """Aggregated usage and cost metrics for a specific time window."""
    period_name: str  # "Today", "Current Week", "Current Month"
    start_time: datetime
    end_time: datetime
    timezone_name: str
    models: Dict[str, ModelUsage] = field(default_factory=dict)
    total_tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    total_cost: CostBreakdown = field(default_factory=CostBreakdown)
    total_cache_savings: float = 0.0
    cost_management_available: bool = False
    cost_management_notes: Optional[str] = None
    estimate_notes: List[str] = field(default_factory=list)
    unattributed_billed_cost: float = 0.0

    def recalculate_totals(self) -> None:
        """Recalculate period totals from model usage entries."""
        tot_tok = TokenBreakdown()
        tot_cost = CostBreakdown()
        tot_savings = 0.0

        for usage in self.models.values():
            tot_tok = tot_tok.add(usage.tokens)
            tot_cost = tot_cost.add(usage.cost)
            tot_savings += usage.cache_savings

        self.total_tokens = tot_tok
        sources = {m.cost.estimate_source for m in self.models.values()}
        tot_cost.estimate_source = next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "rate_card"
        portal_costs = [m.cost.foundry_estimated_cost for m in self.models.values()]
        tot_cost.foundry_estimated_cost = (
            sum(portal_costs) if portal_costs and all(c is not None for c in portal_costs) else None
        )
        if self.unattributed_billed_cost:
            tot_cost.billed_cost = (tot_cost.billed_cost or 0.0) + self.unattributed_billed_cost
        self.total_cost = tot_cost
        self.total_cache_savings = tot_savings


@dataclass
class DeploymentInfo:
    """Azure AI Foundry / OpenAI model deployment metadata."""
    name: str
    model_name: str
    model_version: Optional[str] = None
    sku_name: Optional[str] = None
    capacity: Optional[int] = None


@dataclass
class ResourceInfo:
    """Discovered Azure AI Foundry / OpenAI resource."""
    id: str
    name: str
    resource_group: str
    subscription_id: str
    kind: str
    location: str
    deployments: List[DeploymentInfo] = field(default_factory=list)
