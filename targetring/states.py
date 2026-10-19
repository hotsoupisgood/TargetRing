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


@dataclass(frozen=True)
class TextCell:
    """A four-state reading whose value is a NAME, not a number.

    v4.0, for the chromosome a gene sits on (Puck: "add which chromosome each
    gene is on next to the gene text on the ring"). It is not a ring and it is
    not on a colour scale -- a chromosome is a label, "16" is not more than "7"
    and `Cell` would have to store it as a float to hold it at all.

    What it is NOT is a looser `Cell`. The four states mean exactly what rule 1
    says they mean, the same `__post_init__` discipline applies (a `value` with
    no text, or a `no_data` carrying text, is a construction error rather than
    something the label code has to defend against), and nothing here is ever
    filled in, defaulted or inferred from a neighbouring gene.

    `no_data` renders no suffix at all. A blank suffix is the one rendering of
    `no_data` rule 1 would normally forbid -- it is allowed here, and only here,
    because the alternative is drawing "chr —" on the rim of every figure for
    every gene Open Targets has no location for, and because the drawer header
    and `chromosome.detail` say which of the three non-values it was.
    """

    state: CellState
    text: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state == "value":
            if not self.text:
                raise ValueError("state='value' requires a non-empty text")
        elif self.text is not None:
            raise ValueError(f"state={self.state!r} must not carry text")

    @property
    def available(self) -> bool:
        return self.state == "value"

    def as_dict(self) -> dict[str, Any]:
        """What reaches the drawer JSON. Same three keys a ring cell writes."""
        return {"state": self.state, "text": self.text, "detail": self.detail}


def text_value(text: str, detail: str = "") -> TextCell:
    return TextCell("value", str(text), detail)


def text_no_data(detail: str) -> TextCell:
    return TextCell("no_data", None, detail)


def text_not_applicable(detail: str) -> TextCell:
    return TextCell("not_applicable", None, detail)


def text_failed(detail: str) -> TextCell:
    return TextCell("failed", None, detail)
