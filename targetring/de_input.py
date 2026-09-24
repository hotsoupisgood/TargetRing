"""DE table parsing and the drop ledger.

PLAN.md §4 and QA B7. The DE table is a user's spreadsheet, so every case in
QA B7's table is handled explicitly and every affected gene lands in the ledger
with a reason. The governing invariant, asserted at render time:

    input_rows == rendered_spokes + ledger_entries

Nothing in this module may drop a row silently. Where a row is rejected, it is
rejected *into* the ledger.

Ordering matters: parse -> per-row validation -> resolution -> dedup on the
resolved identifier. Deduplicating on the raw symbol would keep `SEPT9` and
`SEPTIN9` as two spokes for one gene.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .config import MAX_GENES

DropStatus = Literal[
    "ok",
    "renamed",
    "possible_mouse",
    "ambiguous",
    "excel_corrupt",
    "unresolved",
    "dropped_dup",
    "dropped_no_padj",
    "dropped_bad_logfc",
    "dropped_over_cap",
    # v2.5 item 2. Two, not one, and neither is a parse failure or an
    # unresolvable symbol: a gene whose measurement missed a manual threshold and
    # a gene removed because it had no measurement at all are different facts
    # about the figure, and only the second one biases a gene set.
    "filtered",
    "filtered_no_data",
]

RENDERED_STATUSES: frozenset[str] = frozenset({"ok", "renamed", "possible_mouse"})

_SYMBOL_HEADERS = {"gene", "symbol", "gene_symbol", "genesymbol", "gene name", "id", "gene_id"}
_LOGFC_HEADERS = {"logfc", "log2foldchange", "log2fc", "log fc", "lfc", "log2_fold_change"}
_PADJ_HEADERS = {"padj", "adj.p.val", "adj_p_val", "fdr", "qvalue", "q_value", "adjpvalue",
                 "adjusted_p", "adj_pvalue", "p_adj", "adj.pval"}

_BAD_NUMERIC = {"", "na", "n/a", "nan", "null", "none", "#div/0!", "#n/a", "#value!", "-", "."}
_COMMA_DECIMAL_RE = re.compile(r"^-?\d+,\d+$")


class InputRejected(ValueError):
    """The whole file is unusable. Distinct from a per-row drop."""


@dataclass
class DERow:
    """One row of the user's table, as submitted."""

    row_number: int
    symbol: str
    logfc: float
    padj: float


@dataclass
class LedgerEntry:
    input_symbol: str
    status: DropStatus
    reason: str
    resolved_symbol: str | None = None
    ensembl_id: str | None = None
    row_number: int | None = None

    @property
    def rendered(self) -> bool:
        return self.status in RENDERED_STATUSES


@dataclass
class ParsedInput:
    """Everything the run needs, plus the audit trail for what did not survive."""

    rows: list[DERow] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    input_row_count: int = 0
    columns: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add_drop(self, entry: LedgerEntry) -> None:
        self.ledger.append(entry)

    @property
    def status_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.ledger:
            out[e.status] = out.get(e.status, 0) + 1
        return out


# --- decoding ---------------------------------------------------------------


def _is_plausible_text(s: str) -> bool:
    """Reject a decode that "succeeded" into rubbish.

    Latin-1 maps every possible byte, so it never raises -- which would make
    "reject with a named encoding error" unreachable and turn undecodable bytes
    into mojibake gene symbols instead. C1 controls (U+0080-U+009F) and NULs are
    the signal that the bytes were not really Latin-1 text.
    """
    if "\x00" in s:
        return False
    return not any("\x80" <= ch <= "\x9f" for ch in s)


def decode(raw: bytes) -> str:
    """Sniff, decode, or reject with a named encoding error.

    QA B7: never silently replace undecodable bytes -- a replacement character
    in a symbol becomes an unresolvable gene with no explanation.
    """
    attempts: list[tuple[str, bytes]] = []
    if raw[:3] == b"\xef\xbb\xbf":
        attempts.append(("utf-8", raw[3:]))
    elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        attempts.append(("utf-16", raw))
    # UTF-16 is only attempted behind a BOM. Without one it happily decodes
    # arbitrary even-length bytes into plausible-looking CJK and would swallow
    # the rejection path entirely.
    attempts += [("utf-8", raw), ("latin-1", raw)]

    tried: list[str] = []
    for enc, payload in attempts:
        try:
            text = payload.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            tried.append(enc)
            continue
        if _is_plausible_text(text):
            return text
        tried.append(f"{enc} (decoded to control characters)")
    raise InputRejected(
        "Could not decode the file as UTF-8, UTF-16 or Latin-1 "
        f"(tried: {', '.join(dict.fromkeys(tried))}). Re-save it as UTF-8 CSV."
    )


def _norm_header(h: str) -> str:
    return h.strip().lstrip("﻿").strip("\"'").strip().lower()


def _pick(headers: list[str], wanted: set[str], label: str) -> str:
    for h in headers:
        if _norm_header(h) in wanted:
            return h
    raise InputRejected(
        f"No {label} column found. Saw {[_norm_header(h) for h in headers]!r}; "
        f"expected one of {sorted(wanted)!r}. Positional guessing is not attempted."
    )


# --- numeric coercion -------------------------------------------------------


def _detect_comma_decimal(values: Iterable[str]) -> bool:
    """QA B7: per column, not per row. Guessing per row turns 1,23 into 123."""
    seen = [v.strip() for v in values if v.strip().lower() not in _BAD_NUMERIC]
    if not seen:
        return False
    comma = sum(1 for v in seen if _COMMA_DECIMAL_RE.match(v))
    if comma == 0:
        return False
    if comma == len(seen):
        return True
    raise InputRejected(
        f"This column mixes comma and dot decimal separators ({comma} of {len(seen)} rows use a "
        f"comma). Guessing per row would silently turn 1,23 into 123. Fix the export."
    )


def _num(raw: str, comma_decimal: bool) -> float | None:
    v = raw.strip().strip("\"'").strip()
    if v.lower() in _BAD_NUMERIC:
        return None
    if comma_decimal:
        v = v.replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        f = float(v)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        # QA B7: Inf breaks both ranking and colour scaling. Rejected explicitly.
        return None
    return f


# --- the parser -------------------------------------------------------------


def parse_de_table(raw: bytes | str, max_genes: int = MAX_GENES) -> ParsedInput:
    text = decode(raw) if isinstance(raw, bytes) else raw.lstrip("﻿")
    if not text.strip():
        raise InputRejected("The DE table is empty.")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise InputRejected("The DE table has no header row.")
    headers = [h for h in reader.fieldnames if h is not None]

    sym_col = _pick(headers, _SYMBOL_HEADERS, "gene symbol")
    logfc_col = _pick(headers, _LOGFC_HEADERS, "logFC")
    padj_col = _pick(headers, _PADJ_HEADERS, "adjusted p-value")

    records = list(reader)
    parsed = ParsedInput(
        input_row_count=len(records),
        columns={"symbol": sym_col, "logfc": logfc_col, "padj": padj_col},
    )
    if not records:
        raise InputRejected("The DE table has a header but no data rows.")

    logfc_comma = _detect_comma_decimal(r.get(logfc_col) or "" for r in records)
    padj_comma = _detect_comma_decimal(r.get(padj_col) or "" for r in records)
    if logfc_comma or padj_comma:
        parsed.notes.append("Comma decimal separators detected and converted.")

    for i, rec in enumerate(records, start=1):
        symbol = (rec.get(sym_col) or "").strip().strip("\"'").strip()
        if not symbol:
            parsed.add_drop(LedgerEntry("", "unresolved", "blank gene symbol", row_number=i))
            continue

        logfc = _num(rec.get(logfc_col) or "", logfc_comma)
        if logfc is None:
            parsed.add_drop(LedgerEntry(
                symbol, "dropped_bad_logfc",
                f"logFC {(rec.get(logfc_col) or '').strip()!r} is not a finite number "
                f"(NA, Inf and #DIV/0! are rejected, never coerced)",
                row_number=i,
            ))
            continue

        padj = _num(rec.get(padj_col) or "", padj_comma)
        if padj is None:
            # QA B7: coercing to 0 ranks the gene top, to 1 buries it. Both are
            # silent scientific errors, so the row is rejected instead.
            parsed.add_drop(LedgerEntry(
                symbol, "dropped_no_padj",
                f"adjusted p {(rec.get(padj_col) or '').strip()!r} is blank or non-numeric; "
                f"not coerced to 0 or 1",
                row_number=i,
            ))
            continue
        if not 0.0 <= padj <= 1.0:
            parsed.add_drop(LedgerEntry(
                symbol, "dropped_no_padj",
                f"adjusted p {padj} is outside [0, 1]", row_number=i,
            ))
            continue

        parsed.rows.append(DERow(i, symbol, logfc, padj))

    if len(parsed.rows) > max_genes:
        # QA B7: a hard, visible cap. Keeping the strongest padj is a choice, so
        # it is stated, and every excluded gene is named in the ledger.
        kept = sorted(parsed.rows, key=lambda r: (r.padj, -abs(r.logfc)))[:max_genes]
        keep_ids = {r.row_number for r in kept}
        for r in parsed.rows:
            if r.row_number not in keep_ids:
                parsed.add_drop(LedgerEntry(
                    r.symbol, "dropped_over_cap",
                    f"over the {max_genes}-gene cap; kept the top {max_genes} by adjusted p",
                    row_number=r.row_number,
                ))
        parsed.rows = sorted(kept, key=lambda r: r.row_number)
        parsed.notes.append(
            f"Capped at {max_genes} genes by adjusted p. {max_genes} spokes is already "
            f"past readable; this is a labelled choice, not a silent truncation."
        )

    return parsed


def dedupe_on_resolved(
    rows: list[DERow], resolved_key: dict[int, str | None]
) -> tuple[list[DERow], list[LedgerEntry]]:
    """Collapse duplicates on the *resolved* id, keeping max abs(logFC).

    QA B7 says keep max abs(logFC) and report which row was kept. Deduping on
    the raw symbol instead would leave `SEPT9` and `SEPTIN9` as two spokes for
    one gene.
    """
    best: dict[str, DERow] = {}
    order: list[str] = []
    drops: list[LedgerEntry] = []
    passthrough: list[DERow] = []

    for row in rows:
        key = resolved_key.get(row.row_number)
        if key is None:
            passthrough.append(row)  # unresolved rows are ledgered elsewhere
            continue
        cur = best.get(key)
        if cur is None:
            best[key] = row
            order.append(key)
            continue
        loser, winner = (cur, row) if abs(row.logfc) > abs(cur.logfc) else (row, cur)
        best[key] = winner
        drops.append(LedgerEntry(
            loser.symbol, "dropped_dup",
            f"duplicate of {winner.symbol} (row {winner.row_number}) on {key}; kept "
            f"logFC={winner.logfc:g} (larger |logFC|), dropped logFC={loser.logfc:g}",
            ensembl_id=key, row_number=loser.row_number,
        ))

    return [best[k] for k in order] + passthrough, drops


def dropped_entries(ledger: list[LedgerEntry]) -> list[LedgerEntry]:
    """Ledger entries for genes that are *not* on the figure.

    The ledger carries two kinds of entry, and conflating them breaks the
    accounting in both directions. `renamed` and `possible_mouse` genes are
    rendered *and* listed, because the user needs to see that SEPT9 became
    SEPTIN9 on a figure that shows SEPTIN9. Everything else is a gene that is
    not there.
    """
    return [e for e in ledger if not e.rendered]


def assert_accounted(input_rows: int, rendered: int, ledger: list[LedgerEntry]) -> None:
    """AGENTS.md rule 3 / QA B7. The run aborts rather than render a figure that
    has quietly lost a gene."""
    dropped = len(dropped_entries(ledger))
    if input_rows != rendered + dropped:
        flagged = len(ledger) - dropped
        raise AssertionError(
            f"gene accounting broken: {input_rows} input rows != {rendered} rendered "
            f"+ {dropped} dropped. Every input row must be one or the other. "
            f"({flagged} further ledger entries flag rendered genes and must not be "
            f"counted as drops.)"
        )
