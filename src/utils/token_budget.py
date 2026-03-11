"""
src/utils/token_budget.py
──────────────────────────
ContextWindowBudget — tracks cumulative LLM token spend and enforces
tiered model selection as required by the Semanticist agent.

Design
------
* Cheap/fast model  → bulk module summarisation (high volume, low stakes).
* Synthesis model   → Day-One brief, domain clustering (low volume, high quality).
* Hard cap          → raises BudgetExceededError to prevent runaway spend.

Branch: feature/01-project-setup
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

import tiktoken

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

ModelTier = Literal["bulk", "synthesis"]

# ── Approximate costs (USD per 1k tokens) ────────────────────────────────────
_COST_TABLE: dict[str, dict[str, float]] = {
    # model_id: {input, output}  — update when pricing changes
    "claude-haiku-4-5-20251001":        {"input": 0.00025, "output": 0.00125},
    "claude-sonnet-4-6":      {"input": 0.003,   "output": 0.015},
    "gpt-4o-mini":            {"input": 0.00015, "output": 0.0006},
    "gpt-4o":                 {"input": 0.005,   "output": 0.015},
    "gemini-flash":           {"input": 0.000075,"output": 0.0003},
    "mistral-7b-instruct":    {"input": 0.00007, "output": 0.00007},
}

_DEFAULT_HARD_CAP_USD = 5.0   # Halt if total spend exceeds this
_DEFAULT_ENCODING    = "cl100k_base"


class BudgetExceededError(Exception):
    """Raised when the cumulative LLM spend would exceed the hard cap."""


@dataclass
class ContextWindowBudget:
    """
    Thread-safe token / cost tracker for all LLM calls made during a
    single Cartographer run.

    Parameters
    ----------
    bulk_model:
        Model ID used for cheap, high-volume tasks.
    synthesis_model:
        Model ID used for expensive, low-volume synthesis tasks.
    hard_cap_usd:
        Abort threshold. Raises BudgetExceededError if exceeded.
    """

    bulk_model:       str   = "claude-haiku-4-5-20251001"
    synthesis_model:  str   = "claude-sonnet-4-6"
    hard_cap_usd:     float = _DEFAULT_HARD_CAP_USD

    _input_tokens:  int   = field(default=0, init=False, repr=False)
    _output_tokens: int   = field(default=0, init=False, repr=False)
    _total_cost:    float = field(default=0.0, init=False, repr=False)
    _lock:          threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count for *text* using tiktoken."""
        try:
            enc = tiktoken.get_encoding(_DEFAULT_ENCODING)
            return len(enc.encode(text))
        except Exception:  # noqa: BLE001
            # Fallback: rough word-based estimate
            return max(1, len(text.split()) * 4 // 3)

    def select_model(self, tier: ModelTier) -> str:
        """Return the model ID for the given *tier*."""
        return self.bulk_model if tier == "bulk" else self.synthesis_model

    # ── Budget accounting ─────────────────────────────────────────────────────

    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """
        Record token consumption and update the running cost total.

        Raises
        ------
        BudgetExceededError
            If the new total would exceed *hard_cap_usd*.
        """
        costs = _COST_TABLE.get(model, {"input": 0.005, "output": 0.015})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1000

        with self._lock:
            self._input_tokens  += input_tokens
            self._output_tokens += output_tokens
            self._total_cost    += cost

            logger.debug(
                "llm_tokens_recorded",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                call_cost_usd=round(cost, 6),
                total_cost_usd=round(self._total_cost, 4),
            )

            if self._total_cost >= self.hard_cap_usd:
                raise BudgetExceededError(
                    f"LLM spend ${self._total_cost:.4f} has reached the hard cap "
                    f"${self.hard_cap_usd:.2f}. Halting to prevent runaway costs."
                )

    # ── Reporting ─────────────────────────────────────────────────────────────

    @property
    def summary(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "input_tokens":  self._input_tokens,
                "output_tokens": self._output_tokens,
                "total_tokens":  self._input_tokens + self._output_tokens,
                "total_cost_usd": round(self._total_cost, 4),
                "hard_cap_usd":  self.hard_cap_usd,
                "budget_remaining_usd": round(
                    max(0.0, self.hard_cap_usd - self._total_cost), 4
                ),
            }

    def log_summary(self) -> None:
        logger.info("budget_summary", **self.summary)
