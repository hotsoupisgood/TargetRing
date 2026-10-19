"""Six named LLM accounts, six caps, one append-only ledger.

`AGENTS.md` rule 8, amended twice on 2026-10-09 to four paid calls with four
separate caps -- and twice more on 2026-10-18, for the baked per-gene evidence
summary (`summary`) and the baked Claude-coded phenotype flag
(`phenotype_llm`), both of which are spent offline by a script and never by the
app. Rule 8 still says four; those two amendments are Puck's to make -- *"None borrows from another"* -- and to state that a cap is an
approval gate rather than a ceiling: it stops, names the numbers, records the
refusal, and raising it is Puck's. Before this
module the caps were constants with nothing behind them and `manifest.llm` was
the literal `{"attempted": False, "spend_usd": 0.0}`. A cap with no counter is a
number in a file, and a manifest that reports a hard-coded zero is the
accounting version of a plausible wrong figure: it reads like a measurement.

Five decisions here are load-bearing, and each is the one that stops a spend
figure from being plausible and wrong.

1. **Accounts resolved by name, and an unknown name raises.** The caps are
   read from `config` at call time rather than captured at import, so a test can
   move one cap and the other is provably untouched. There is no "default
   account": a call that does not say which budget it spends has no business
   spending one.

2. **The ledger is append-only, and it is under `runs/`.** `cache/` is wiped
   routinely in this repo; a counter that a cache reset zeroes is not a counter.
   Nothing in this module rewrites or truncates a line. `spent()` is a fold over
   the file, so the file *is* the state and a restart cannot lose it.

3. **Reserve worst-case before the call, commit actual after.** The reservation
   line is written *before* the request goes out, with `status="unknown"` and the
   worst-case projection as its `usd`. If the process dies mid-call that line
   survives and is counted as spent, so a crash over-counts rather than
   under-counts. `commit` appends a second line for the same `reservation_id`
   and that line supersedes the first when the fold runs.

4. **Refusals are recorded.** A cap refusal costs $0 and still gets a line, so
   "why is there no summary" is answerable from the file rather than from
   somebody's memory of a banner. A *validation* refusal costs whatever the call
   cost -- the tokens were spent whether or not the answer was usable.

5. **An unpriced model cannot be called.** `price_usd` raises `UnpricedModel`
   rather than defaulting to zero or to a neighbouring model's price. The price
   table is pinned in `config` and copied into every manifest, because the one
   failure this module cannot detect is the published price moving underneath a
   pinned constant.

No paid call is made anywhere in this file. It is the accounting, not the caller.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

TRIAGE = "triage"
PHENOTYPE = "phenotype"
CHOOSER = "chooser"
REGION = "region"
#: 2026-10-18: the baked per-gene evidence summary (`summary_llm.py`). Spent
#: only by `scripts/bake_summaries.py`, offline; the app never calls it.
SUMMARY = "summary"
#: 2026-10-18 18:42: the Claude-coded 0/1 phenotype flag (`phenotype_llm.py`).
#: Spent only by `scripts/bake_phenotype_flags.py`, offline. Distinct from
#: `PHENOTYPE` above, which is the phenotype-PAGE summary; see the note in
#: `config.py` on why the two names are this close.
PHENOTYPE_LLM = "phenotype_llm"

#: The only account names there are. The value is the `config` attribute
#: holding that account's cap, looked up when asked rather than copied here, so
#: the cap has exactly one definition.
ACCOUNT_CAP_ATTR: dict[str, str] = {
    TRIAGE: "LLM_SPEND_CAP_USD",
    PHENOTYPE: "PHENOTYPE_LLM_SPEND_CAP_USD",
    CHOOSER: "CHOOSER_LLM_SPEND_CAP_USD",
    REGION: "REGION_LLM_SPEND_CAP_USD",
    SUMMARY: "SUMMARY_LLM_SPEND_CAP_USD",
    PHENOTYPE_LLM: "PHENOTYPE_FLAG_SPEND_CAP_USD",
}

ACCOUNTS: tuple[str, ...] = (TRIAGE, PHENOTYPE, CHOOSER, REGION, SUMMARY,
                             PHENOTYPE_LLM)

#: What an account is for, printed beside its line in the manifest and the UI so
#: two identical `$0.00 of $5.00` rows are distinguishable.
ACCOUNT_PURPOSE: dict[str, str] = {
    TRIAGE: (f"LLM triage of the top {config.TRIAGE_TOP_N} genes (not built; "
             f"nothing has ever been spent on it)"),
    PHENOTYPE: ("the phenotype-page summary, one call per gene, summarising "
                "only the table it sits above"),
    CHOOSER: ("the contrast chooser, one call per disease, choosing only among "
              "contrasts the index already offered it"),
    REGION: ("the region chooser, one call per disease and then cached, "
             "choosing only among the GTEx tissues this build can read"),
    SUMMARY: ("the per-gene evidence summary, baked offline by "
              "scripts/bake_summaries.py from the gene's own drawer values and "
              "served as a file; the app never makes this call"),
    PHENOTYPE_LLM: ("the Claude-coded 0/1 phenotype flag, baked offline by "
                    "scripts/bake_phenotype_flags.py from the gene's mouse "
                    "phenotype terms and the contrast's disease area; a display "
                    "aid beside the owner's phenotype criterion, never the "
                    "criterion, and the app never makes this call"),
}

#: Terminal statuses. A line carrying one of these is the last word on its
#: reservation; `unknown` means the reservation was opened and never closed.
STATUS_OK = "ok"
STATUS_REFUSED_CAP = "refused_cap"
STATUS_REFUSED_VALIDATION = "refused_validation"
STATUS_API_ERROR = "api_error"
STATUS_UNKNOWN = "unknown"

TERMINAL_STATUSES = frozenset(
    {STATUS_OK, STATUS_REFUSED_VALIDATION, STATUS_API_ERROR, STATUS_REFUSED_CAP})

ALL_STATUSES = frozenset(TERMINAL_STATUSES | {STATUS_UNKNOWN})


class SpendError(RuntimeError):
    """Base for every refusal in this module. Never caught to make a call."""


class UnknownAccount(SpendError):
    """A name that is not one of the accounts."""


class UnpricedModel(SpendError):
    """No pinned price for this model, so its spend cannot be computed.

    Deliberately a refusal and not a zero. A call whose cost cannot be measured
    is a call that spends an unmeasured amount of a $5 budget.
    """


class CapExceeded(SpendError):
    """The worst-case projection would cross this account's cap.

    Raised *before* the request is made, and the refusal is in the ledger.
    """


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cap_for(account: str) -> float:
    """This account's cap, read from `config` now rather than at import."""
    attr = ACCOUNT_CAP_ATTR.get(account)
    if attr is None:
        raise UnknownAccount(
            f"{account!r} is not an LLM spend account. The accounts are "
            f"{sorted(ACCOUNT_CAP_ATTR)} (AGENTS.md rule 8); there is no default "
            f"and no shared pool.")
    return float(getattr(config, attr))


def price_for(model: str) -> dict[str, float]:
    """The pinned price row for a model, or a refusal.

    `config.LLM_PRICE_USD_PER_MTOK` is the only table. There is no fallback to a
    similarly-named model: `claude-opus-5` and `claude-fable-5-1` differ by 2x,
    so a near-miss would not be a rounding error.
    """
    row = config.LLM_PRICE_USD_PER_MTOK.get(model)
    if row is None:
        raise UnpricedModel(
            f"no pinned price for model {model!r}. Priced models are "
            f"{sorted(config.LLM_PRICE_USD_PER_MTOK)}. Add the published prices to "
            f"config.LLM_PRICE_USD_PER_MTOK -- a guessed price is a spend figure "
            f"that looks measured and is not.")
    return row


def price_usd(model: str, *, input_tokens: int = 0, output_tokens: int = 0,
              cache_read_tokens: int = 0) -> float:
    """USD for one call's token counts, from the pinned table.

    A `cache_read_tokens` count on a model whose table row carries no
    `cache_read` price is refused rather than billed at zero or at the input
    price. An absent price is not a price of zero.
    """
    row = price_for(model)
    usd = (input_tokens * row["input"] + output_tokens * row["output"]) / 1e6
    if cache_read_tokens:
        if "cache_read" not in row:
            raise UnpricedModel(
                f"{model!r} reported {cache_read_tokens} cache-read tokens and "
                f"config.LLM_PRICE_USD_PER_MTOK[{model!r}] has no 'cache_read' "
                f"price. Refusing to bill them at zero.")
        usd += cache_read_tokens * row["cache_read"] / 1e6
    return usd


def project_usd(model: str, *, input_tokens: int, max_tokens: int,
                cache_read_tokens: int = 0) -> float:
    """The worst case: every prompt token billed, every output token allowed.

    The cap is checked against this rather than against an expected cost, so it
    trips early rather than late. The ledger then records what the call really
    cost, and the difference between the two is visible in the file.
    """
    return price_usd(model, input_tokens=input_tokens, output_tokens=max_tokens,
                     cache_read_tokens=cache_read_tokens)


@dataclass(frozen=True)
class Reservation:
    """An opened reservation. Carried from `reserve` to `commit`, not re-derived."""

    reservation_id: str
    account: str
    model: str
    projected_usd: float
    prompt_hash: str
    key: str


@dataclass(frozen=True)
class AccountState:
    """One account's numbers, as the manifest and the UI print them."""

    account: str
    cap_usd: float
    spent_usd: float
    calls: int
    purpose: str

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def as_dict(self) -> dict[str, Any]:
        return {"cap": round(self.cap_usd, 6), "spent": round(self.spent_usd, 6),
                "calls": self.calls, "remaining": round(self.remaining_usd, 6),
                "purpose": self.purpose}

    @property
    def line(self) -> str:
        """`phenotype: $0.00 of $5.00 spent over 0 calls` -- one account, one line."""
        return (f"{self.account}: ${self.spent_usd:.2f} of ${self.cap_usd:.2f} "
                f"spent over {self.calls} call{'' if self.calls == 1 else 's'}")


class SpendLedger:
    """The append-only file, and the only way spend is counted.

    `spent()` folds the file every time it is asked. That is deliberately not
    cached in memory: two processes (the server and a script) may both append,
    and an in-memory total would let one of them spend against a stale figure.
    The file is small -- one short line per call -- and correctness is the point.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or config.SPEND_LEDGER_PATH)

    # --- reading -----------------------------------------------------------

    def lines(self) -> list[dict[str, Any]]:
        """Every well-formed record, oldest first.

        A truncated final line -- the shape a crash mid-append leaves -- is
        *reported*, not skipped: `CorruptLedgerLine` in the returned list would
        be tidier and would also let a partially written $4.90 reservation vanish
        from the total. Instead an undecodable line raises, because the one thing
        this file may never do is under-count.
        """
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for n, raw in enumerate(self.path.read_text().splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                raise SpendError(
                    f"{self.path}:{n} is not decodable JSON ({e}). The spend ledger "
                    f"is append-only and is the only record of what has been spent; "
                    f"it is not repaired automatically and no call is made while it "
                    f"cannot be read.") from e
            if not isinstance(rec, dict):
                raise SpendError(f"{self.path}:{n} is a {type(rec).__name__}, not an object")
            out.append(rec)
        return out

    def _folded(self) -> Iterator[dict[str, Any]]:
        """One record per reservation: the terminal line where there is one.

        This is what makes an append-only file a balance. A reservation and its
        commit are two lines with one `reservation_id`; counting both would
        double-charge, and counting only the first would report the worst case as
        the actual. The last terminal line wins; a reservation with none is
        yielded as it stands, `status="unknown"`, at its worst-case projection.
        """
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for rec in self.lines():
            rid = str(rec.get("reservation_id") or f"_anon{len(order)}")
            if rid not in by_id:
                by_id[rid] = rec
                order.append(rid)
                continue
            if rec.get("status") in TERMINAL_STATUSES:
                by_id[rid] = rec
        for rid in order:
            yield by_id[rid]

    def records_for(self, account: str) -> list[dict[str, Any]]:
        cap_for(account)  # rejects an unknown name before it can return []
        return [r for r in self._folded() if r.get("account") == account]

    def spent(self, account: str) -> float:
        """USD charged to this account, and to no other."""
        return sum(float(r.get("usd") or 0.0) for r in self.records_for(account))

    def calls(self, account: str) -> int:
        """Lines on this account, refusals included.

        A refused call is a call: it is the answer to "why is there no summary",
        and hiding it from the count would make the ledger agree with the screen
        only when nothing went wrong.
        """
        return len(self.records_for(account))

    def state(self, account: str) -> AccountState:
        return AccountState(account=account, cap_usd=cap_for(account),
                            spent_usd=self.spent(account), calls=self.calls(account),
                            purpose=ACCOUNT_PURPOSE[account])

    def states(self) -> dict[str, AccountState]:
        return {a: self.state(a) for a in ACCOUNTS}

    # --- writing -----------------------------------------------------------

    def _append(self, rec: dict[str, Any]) -> dict[str, Any]:
        if rec["status"] not in ALL_STATUSES:
            raise SpendError(f"unknown ledger status {rec['status']!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, sort_keys=True)
        # One `write` of one line, opened append-only, flushed and fsynced. The
        # reservation must be on disk before the request leaves, or a crash
        # during the call loses the charge -- which is the one direction this
        # file is not allowed to be wrong in.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

    def _record(self, *, account: str, model: str, status: str, usd: float,
                key: str, prompt_hash: str, reservation_id: str,
                input_tokens: int = 0, cache_read_tokens: int = 0,
                output_tokens: int = 0, note: str = "") -> dict[str, Any]:
        cap_for(account)
        return self._append({
            "timestamp_utc": _utc_now(),
            "account": account,
            "model": model,
            "prompt_hash": prompt_hash,
            "input_tokens": int(input_tokens),
            "cache_read_tokens": int(cache_read_tokens),
            "output_tokens": int(output_tokens),
            "usd": round(float(usd), 8),
            "key": key,
            "status": status,
            "reservation_id": reservation_id,
            **({"note": note} if note else {}),
        })

    def reserve(self, *, account: str, model: str, input_tokens: int,
                max_tokens: int, key: str, prompt_hash: str,
                cache_read_tokens: int = 0) -> Reservation:
        """Charge the worst case to the account, or refuse and record why.

        Raises `UnpricedModel` before anything is written -- an unpriced model is
        not a cap problem and must not land in the file as one. Raises
        `CapExceeded` *after* writing the refusal, so the file answers the
        question the banner raises.
        """
        projected = project_usd(model, input_tokens=input_tokens,
                                max_tokens=max_tokens,
                                cache_read_tokens=cache_read_tokens)
        rid = uuid.uuid4().hex[:16]
        state = self.state(account)
        if state.spent_usd + projected > state.cap_usd:
            self._record(account=account, model=model, status=STATUS_REFUSED_CAP,
                         usd=0.0, key=key, prompt_hash=prompt_hash,
                         reservation_id=rid, input_tokens=input_tokens,
                         cache_read_tokens=cache_read_tokens,
                         note=(f"worst case ${projected:.4f} on top of "
                               f"${state.spent_usd:.4f} would cross the "
                               f"${state.cap_usd:.2f} {account} cap"))
            raise CapExceeded(
                f"{account} LLM budget: ${state.spent_usd:.2f} of "
                f"${state.cap_usd:.2f} already spent, and this call's worst case "
                f"is ${projected:.4f}. No call was made; the refusal is in "
                f"{self.path.name}.")
        self._record(account=account, model=model, status=STATUS_UNKNOWN,
                     usd=projected, key=key, prompt_hash=prompt_hash,
                     reservation_id=rid, input_tokens=input_tokens,
                     cache_read_tokens=cache_read_tokens,
                     note="reserved worst case; not yet committed")
        return Reservation(reservation_id=rid, account=account, model=model,
                           projected_usd=projected, prompt_hash=prompt_hash, key=key)

    def commit(self, reservation: Reservation, *, input_tokens: int,
               output_tokens: int, cache_read_tokens: int = 0,
               status: str = STATUS_OK, note: str = "") -> dict[str, Any]:
        """Replace a reservation's worst case with what the call really cost.

        `status` is the caller's outcome, not this module's guess:
        `refused_validation` still charges the real usage (the tokens were spent),
        `api_error` charges what the response reported, which for a failed call
        is usually nothing.
        """
        if status not in TERMINAL_STATUSES or status == STATUS_REFUSED_CAP:
            raise SpendError(
                f"commit status must be one of "
                f"{sorted(TERMINAL_STATUSES - {STATUS_REFUSED_CAP})}, not {status!r}")
        usd = price_usd(reservation.model, input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens)
        return self._record(
            account=reservation.account, model=reservation.model, status=status,
            usd=usd, key=reservation.key, prompt_hash=reservation.prompt_hash,
            reservation_id=reservation.reservation_id, input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens, output_tokens=output_tokens,
            note=note or f"committed against reserved ${reservation.projected_usd:.4f}")

    def record_refusal(self, *, account: str, model: str, key: str,
                       prompt_hash: str, reason: str,
                       status: str = STATUS_REFUSED_CAP) -> dict[str, Any]:
        """A $0 refusal with no reservation behind it -- the ledger's answer to
        "nothing happened, why?". Used where the decision precedes the reserve."""
        return self._record(account=account, model=model, status=status, usd=0.0,
                            key=key, prompt_hash=prompt_hash,
                            reservation_id=uuid.uuid4().hex[:16], note=reason)

    # --- manifest ----------------------------------------------------------

    def manifest_block(self, *, model: str | None = None,
                       prompt_hash: str | None = None,
                       attempted: bool = False,
                       note: str = "") -> dict[str, Any]:
        """The `llm` block every manifest carries.

        Both accounts every time, whether or not this run touched either: a
        manifest that named only the account a run spent from would make the
        other one's balance unreadable from the artefact, and rule 8's promise is
        that the two do not borrow from each other -- which is a claim about both
        numbers.
        """
        return {
            "accounts": {a: s.as_dict() for a, s in self.states().items()},
            "lines": [s.line for s in self.states().values()],
            "model": model,
            "prompt_hash": prompt_hash,
            "price_table": {m: dict(row) for m, row
                            in config.LLM_PRICE_USD_PER_MTOK.items()},
            "price_table_cached_utc": "2026-06-24",
            "ledger_path": str(self.path),
            "attempted": attempted,
            "note": note,
        }


def default_ledger() -> SpendLedger:
    return SpendLedger()


def manifest_block(**kw: Any) -> dict[str, Any]:
    """Convenience for callers that only want the block for a run."""
    return default_ledger().manifest_block(**kw)


__all__ = [
    "ACCOUNTS", "ACCOUNT_CAP_ATTR", "ACCOUNT_PURPOSE", "AccountState",
    "CapExceeded", "PHENOTYPE", "Reservation", "SpendError", "SpendLedger",
    "STATUS_API_ERROR", "STATUS_OK", "STATUS_REFUSED_CAP",
    "STATUS_REFUSED_VALIDATION", "STATUS_UNKNOWN", "TRIAGE", "UnknownAccount",
    "UnpricedModel", "cap_for", "default_ledger", "manifest_block", "price_for",
    "price_usd", "project_usd",
]
