"""Quota circuit breaker: halt passes before the weekly budget runs out.

Design: docs/design/prompt-laddering.md §"Usage circuit-breaking". A
`quota_check()` callable is injected into Runner (checked before each
batch) and Researcher (session start + each tool round). The default
probe reads `omp usage --json --provider kimi-code` and extracts the
7-day window's usedFraction. Threshold default 0.50 (Matt, 2026-09-13:
early-week headroom).

Fail-safe: probe failure raises QuotaProbeError and halts — headroom is
never assumed. Halt leaves checkpoints untouched, so a later resume
re-attempts the interrupted batch/chunk. Disabled by passing None.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

DEFAULT_THRESHOLD = 0.50
PROBE_COMMAND = ["omp", "usage", "--json", "--provider", "kimi-code"]


class QuotaProbeError(Exception):
    """The quota probe itself failed (omp error, unparseable output)."""


class QuotaExceeded(Exception):
    """Weekly usage met/exceeded the configured threshold — halt cleanly."""


def probe_weekly_fraction(
    command: list[str] | None = None, timeout: float = 15.0
) -> float:
    """usedFraction of the 7-day quota window. Raises QuotaProbeError on
    any failure — callers must treat that as a halt, never as headroom."""
    try:
        proc = subprocess.run(
            command or PROBE_COMMAND,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QuotaProbeError(f"quota probe failed: {exc}") from exc
    if proc.returncode != 0:
        raise QuotaProbeError(f"quota probe exited {proc.returncode}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise QuotaProbeError(f"quota probe output is not JSON: {exc}") from exc
    try:
        for provider_report in report["reports"]:
            for limit in provider_report["limits"]:
                if limit["window"]["id"] == "7d":
                    return float(limit["amount"]["usedFraction"])
    except (KeyError, TypeError, ValueError) as exc:
        raise QuotaProbeError(f"quota probe output missing fields: {exc}") from exc
    raise QuotaProbeError("quota probe output has no 7-day window")


def make_quota_breaker(
    threshold: float = DEFAULT_THRESHOLD,
    probe: Callable[[], float] = probe_weekly_fraction,
) -> Callable[[], None]:
    """A quota_check() that raises QuotaExceeded at/over the threshold."""

    def check() -> None:
        fraction = probe()
        if fraction >= threshold:
            raise QuotaExceeded(
                f"weekly quota at {fraction:.0%} >= breaker threshold "
                f"{threshold:.0%} — halting with checkpoint preserved"
            )

    return check
