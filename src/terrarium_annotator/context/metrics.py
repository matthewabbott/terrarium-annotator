"""Context usage metrics for observability."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextMetrics:
    """Metrics for a single scene's context usage."""

    scene_id: str
    total_tokens: int
    budget: int
    compaction_triggered: bool = False
    tier_activated: int | None = None  # 1-4 or None if no compaction

    # Breakdown by message type
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def usage_percent(self) -> float:
        """Context usage as percentage of budget."""
        if self.budget == 0:
            return 0.0
        return (self.total_tokens / self.budget) * 100


@dataclass
class CompactionStats:
    """Aggregate compaction statistics across a run."""

    tier_activations: dict[int, int] = field(
        default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0}
    )
    total_compactions: int = 0
    total_tokens_saved: int = 0

    # Track context usage over time
    usage_samples: list[float] = field(default_factory=list)

    def record_compaction(
        self, tier: int, tokens_before: int, tokens_after: int
    ) -> None:
        """Record a compaction event."""
        self.tier_activations[tier] = self.tier_activations.get(tier, 0) + 1
        self.total_compactions += 1
        self.total_tokens_saved += tokens_before - tokens_after

    def record_usage(self, usage_percent: float) -> None:
        """Record a context usage sample."""
        self.usage_samples.append(usage_percent)

    @property
    def avg_usage_percent(self) -> float:
        """Average context usage across all samples."""
        if not self.usage_samples:
            return 0.0
        return sum(self.usage_samples) / len(self.usage_samples)

    @property
    def max_usage_percent(self) -> float:
        """Maximum context usage observed."""
        if not self.usage_samples:
            return 0.0
        return max(self.usage_samples)

    def summary(self) -> str:
        """Human-readable summary of compaction stats."""
        tiers = self.tier_activations
        return (
            f"Compactions: {self.total_compactions} "
            f"(T1={tiers.get(1, 0)} T2={tiers.get(2, 0)} "
            f"T3={tiers.get(3, 0)} T4={tiers.get(4, 0)}) | "
            f"Tokens saved: {self.total_tokens_saved} | "
            f"Avg usage: {self.avg_usage_percent:.1f}% | "
            f"Max usage: {self.max_usage_percent:.1f}%"
        )
