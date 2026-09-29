"""Manual threshold filters, one per data source, that remove genes from the plot.

v2.5 item 2. Two rules govern this whole module, and both are asserted rather
than described:

1. **A threshold never silently drops a gene with no data on that ring.** If the
   ring has no value for a gene the filter cannot be evaluated, so the gene is
   *kept* and *marked*. Dropping it would make filtering exclusion-by-missingness
   and would systematically delete under-studied genes -- `docs/reviews/
   COUNCIL-v0-plan.md` M3's bias, arriving through a UI control instead of
   through an imputation. A separate, explicit per-filter toggle
   (`drop_no_data`) removes them, and then the figure says that is what happened.
2. **A filtered gene goes in the drop ledger**, under its own statuses, so it is
   distinguishable from a parse failure and from an unresolvable symbol, and
   `input == rendered + ledgered` still holds.

Thresholds are compared against the ring's own raw value -- the number in
`values.csv`, in the units the ring's label states -- never against a rank or a
scaled colour position. `regional_specificity` is a TPM ratio, so `>= 5` means
five-fold; `neg_log10_padj` is -log10(adjusted p), so `>= 3` means p <= 0.001.
"""

from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .states import Cell

# Two operators, both inclusive. A threshold a user types is a bound they want
# kept, and "greater than but not equal" is a distinction nobody wants to debug
# from a figure.
OPS: dict[str, tuple[str, Callable[[float, float], bool]]] = {
    "ge": (">=", operator.ge),
    "le": ("<=", operator.le),
}
# Public because `dim.py` reads the same table: the v3.8 dim filter is a separate
# mechanism with a separate consequence, but if `>=` meant one thing there and
# another here the app would hold two definitions of a threshold.
SYMBOL_TO_OP = {">=": "ge", "=>": "ge", ">": "ge", "<=": "le", "=<": "le", "<": "le"}
_SYMBOL_TO_OP = SYMBOL_TO_OP  # the name this module was written with

# Statuses these filters add to the drop ledger. Deliberately two, not one: a
# gene removed because its measurement missed a threshold and a gene removed
# because it had no measurement at all are different facts about the figure, and
# the second is the one that biases a gene set.
FILTERED = "filtered"
FILTERED_NO_DATA = "filtered_no_data"
FILTER_STATUSES: frozenset[str] = frozenset({FILTERED, FILTERED_NO_DATA})

# The trailing `drop_no_data` is rule 1's opt-out, written out in full on the
# line it applies to. There is no short form and no default: a control that
# removes under-studied genes should have to be spelled.
_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*(>=|=>|<=|=<|>|<)\s*(-?\d+(?:\.\d+)?)"
                   r"(\s+drop_no_data)?\s*$")


class FilterRejected(ValueError):
    """The filter settings themselves are unusable. Not a per-gene drop."""


@dataclass(frozen=True)
class Filter:
    ring: str
    op: str
    threshold: float
    # Rule 1's explicit opt-out, per filter and never a default.
    drop_no_data: bool = False

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise FilterRejected(
                f"unknown comparison {self.op!r} on the {self.ring!r} filter; "
                f"use one of {sorted(OPS)}")
        if self.threshold != self.threshold or self.threshold in (
                float("inf"), float("-inf")):
            raise FilterRejected(f"{self.ring!r}: {self.threshold!r} is not a threshold")

    @property
    def symbol(self) -> str:
        return OPS[self.op][0]

    def keeps(self, v: float) -> bool:
        return OPS[self.op][1](v, self.threshold)

    def label(self, ring_label: str | None = None) -> str:
        """What the figure prints. The ring's own label where one is available, so
        a reader sees the units rather than an internal key."""
        base = f"{ring_label or self.ring} {self.symbol} {self.threshold:g}"
        return base + (" (+ drop no-data)" if self.drop_no_data else "")

    def as_dict(self) -> dict[str, Any]:
        return {"ring": self.ring, "op": self.op, "symbol": self.symbol,
                "threshold": self.threshold, "drop_no_data": self.drop_no_data,
                "expression": f"{self.ring} {self.symbol} {self.threshold:g}"}


def parse_filters(raw: Any, filterable: Iterable[str] = ()) -> list[Filter]:
    """Filters from the app, as JSON from the form or as one `ring >= n` per line.

    A filter naming a ring this figure does not have is rejected outright: it
    would otherwise be printed on the figure as active while removing nothing.
    """
    if raw is None or raw == "":
        return []
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
            except json.JSONDecodeError as e:
                raise FilterRejected(f"filters are not valid JSON: {e}") from e
        else:
            items = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                m = _LINE.match(line)
                if not m:
                    raise FilterRejected(
                        f"cannot read the filter {line.strip()!r}. One per line, as "
                        f"`ring >= number` or `ring <= number`, optionally followed by "
                        f"`drop_no_data`.")
                items.append({"ring": m.group(1), "op": _SYMBOL_TO_OP[m.group(2)],
                              "threshold": float(m.group(3)),
                              "drop_no_data": bool(m.group(4))})
    else:
        raise FilterRejected(f"filters must be a list or text, not {type(raw).__name__}")

    known = set(filterable)
    out: list[Filter] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FilterRejected(f"each filter must be an object, not {item!r}")
        ring = str(item.get("ring") or "").strip()
        op = str(item.get("op") or "ge").strip()
        op = _SYMBOL_TO_OP.get(op, op)
        raw_threshold = item.get("threshold")
        if raw_threshold in (None, ""):
            continue  # an empty box on the form is not a filter
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise FilterRejected(
                f"{ring or '?'}: {raw_threshold!r} is not a number") from e
        if known and ring not in known:
            raise FilterRejected(
                f"there is no {ring!r} ring to filter on. Filterable rings: "
                f"{', '.join(sorted(known))}.")
        if (ring, op) in seen:
            raise FilterRejected(
                f"two {ring!r} {OPS[op][0] if op in OPS else op} filters were submitted; "
                f"keep one so the figure can state what it did")
        seen.add((ring, op))
        out.append(Filter(ring=ring, op=op, threshold=threshold,
                          drop_no_data=bool(item.get("drop_no_data"))))
    return out


@dataclass
class Drop:
    gene: str
    ring: str
    status: str
    reason: str


@dataclass
class FilterOutcome:
    kept: list[str]
    drops: list[Drop]
    # gene -> rings where a filter was active and could not be evaluated. These
    # genes are on the figure and marked; rule 1 is what they are evidence of.
    kept_unevaluated: dict[str, list[str]]

    @property
    def n_dropped(self) -> int:
        return len(self.drops)


def apply(
    genes: Sequence[str],
    cells_by_ring: dict[str, dict[str, Cell]],
    filters: Sequence[Filter],
) -> FilterOutcome:
    """Decide, per gene, against every active filter. Nothing is mutated here."""
    if not filters:
        return FilterOutcome(kept=list(genes), drops=[], kept_unevaluated={})

    kept: list[str] = []
    drops: list[Drop] = []
    unevaluated: dict[str, list[str]] = {}
    for gene in genes:
        failed: list[Drop] = []
        skipped: list[str] = []
        for f in filters:
            cell = cells_by_ring.get(f.ring, {}).get(gene)
            if cell is None or cell.state != "value":
                state = "absent from this figure" if cell is None else cell.state
                if f.drop_no_data:
                    failed.append(Drop(
                        gene, f.ring, FILTERED_NO_DATA,
                        f"{f.ring} is {state} for this gene and the "
                        f"'{f.label()}' filter was set to drop genes with no data. "
                        f"Without that toggle it would have been kept and marked.",
                    ))
                else:
                    skipped.append(f.ring)
                continue
            if not f.keeps(cell.value):
                failed.append(Drop(
                    gene, f.ring, FILTERED,
                    f"{f.ring} = {cell.value:g}, which fails the filter "
                    f"{f.ring} {f.symbol} {f.threshold:g}",
                ))
        if failed:
            # One ledger row per gene, naming every filter it failed, so the
            # ledger stays one-row-per-input-row and the accounting still adds up.
            status = (FILTERED if any(d.status == FILTERED for d in failed)
                      else FILTERED_NO_DATA)
            drops.append(Drop(gene, failed[0].ring, status,
                              "; ".join(d.reason for d in failed)))
            continue
        kept.append(gene)
        if skipped:
            unevaluated[gene] = skipped
    return FilterOutcome(kept=kept, drops=drops, kept_unevaluated=unevaluated)


def restrict(cells_by_ring: dict[str, dict[str, Cell]],
             kept: Sequence[str]) -> dict[str, dict[str, Cell]]:
    """The same rings over the kept genes only. A filtered gene must not survive
    in one ring's dict and vanish from another's -- that is the cell-count
    invariant failing one render later."""
    keep = set(kept)
    return {ring: {g: c for g, c in cells.items() if g in keep}
            for ring, cells in cells_by_ring.items()}


def filter_lines(filters: Sequence[Filter],
                 ring_labels: dict[str, str] | None = None) -> list[str]:
    return [f.label((ring_labels or {}).get(f.ring)) for f in filters]


def as_dicts(filters: Sequence[Filter]) -> list[dict[str, Any]]:
    return [f.as_dict() for f in filters]


__all__ = ["Filter", "FilterOutcome", "FilterRejected", "Drop", "OPS",
           "SYMBOL_TO_OP", "FILTERED", "FILTERED_NO_DATA", "FILTER_STATUSES",
           "parse_filters", "apply", "restrict", "filter_lines", "as_dicts"]
