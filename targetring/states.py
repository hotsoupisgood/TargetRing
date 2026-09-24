"""The four-state cell, and nothing else.

AGENTS.md rule 1: every ring has four states and `no_data` is never filled,
averaged, zeroed or imputed. Making this a type rather than a convention is the
cheapest way to stop a None becoming a 0.0 three call frames later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CellState = Literal["value", "no_data", "not_applicable", "failed"]

ALL_STATES: tuple[CellState, ...] = ("value", "no_data", "not_applicable", "failed")


@dataclass(frozen=True)
class Cell:
    """One gene x one ring.

    `value` is populated only when state == "value". Anything else carries None
    and a human-readable `detail` that reaches the CSV and the drawer.
    """

    state: CellState
    value: float | None = None
    detail: str = ""
    raw: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.state == "value":
            if self.value is None:
                raise ValueError("state='value' requires a value")
            if self.value != self.value:  # NaN
                raise ValueError("NaN is not a value; use no_data")
            if self.value in (float("inf"), float("-inf")):
                raise ValueError("infinite value; use failed with a reason")
        elif self.value is not None:
            raise ValueError(f"state={self.state!r} must not carry a value")

    @property
    def available(self) -> bool:
        """Counts toward k. Only a real measurement does."""
        return self.state == "value"


def value(v: float, detail: str = "", raw: Any = None) -> Cell:
    return Cell("value", float(v), detail, raw)


def no_data(detail: str, raw: Any = None) -> Cell:
    """Measured nowhere / gene absent from this source. Hatched, off-colormap."""
    return Cell("no_data", None, detail, raw)


def not_applicable(detail: str, raw: Any = None) -> Cell:
    """The question does not apply to this gene (e.g. non-coding, no protein)."""
    return Cell("not_applicable", None, detail, raw)


def failed(detail: str, raw: Any = None) -> Cell:
    """We asked and the call broke. Distinct from the source saying nothing."""
    return Cell("failed", None, detail, raw)
