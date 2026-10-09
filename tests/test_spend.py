"""Stage 0 — the spend ledger and its per-account caps.

What these tests are actually defending, in one sentence each:

* the caps are **separate counters**, not one pool with several names;
* a call that would cross a cap is refused **and the refusal is in the file**,
  because "the summary is missing" must be answerable from an artefact;
* the file survives a restart, because a spend counter that lives in a process is
  not a spend counter;
* an **unpriced model is refused, not guessed at**, because a guessed price is a
  spend figure that looks measured and is not;
* every manifest carries **every** account, because rule 8's promise ("none
  borrows from another") is a claim about each of those numbers.

No paid call is made anywhere in this file, and none is mocked either: stage 0
builds the accounting, not the caller.
"""

from __future__ import annotations

import json

import pytest

from targetring import config, manifest as manifest_mod, spend
from targetring.invariants import InvariantReport


@pytest.fixture()
def ledger(tmp_path):
    return spend.SpendLedger(tmp_path / "spend-ledger.jsonl")


OPUS = "claude-opus-5"


def _reserve(led, account, *, input_tokens=1000, max_tokens=1200, key="k",
             model=OPUS):
    return led.reserve(account=account, model=model, input_tokens=input_tokens,
                       max_tokens=max_tokens, key=key, prompt_hash="ph0")


# --------------------------------------------------------------------------
# accounts and names
# --------------------------------------------------------------------------

class TestAccounts:
    def test_the_accounts_are_the_rule_8_names(self) -> None:
        """Four since 2026-10-09, which added the contrast chooser and then the
        region chooser."""
        assert spend.ACCOUNTS == ("triage", "phenotype", "chooser", "region")

    def test_each_account_reads_its_own_config_cap(self) -> None:
        assert spend.cap_for("triage") == config.LLM_SPEND_CAP_USD
        assert spend.cap_for("phenotype") == config.PHENOTYPE_LLM_SPEND_CAP_USD
        assert spend.cap_for("chooser") == config.CHOOSER_LLM_SPEND_CAP_USD
        assert spend.cap_for("region") == config.REGION_LLM_SPEND_CAP_USD

    def test_no_account_shares_a_cap_with_another(self) -> None:
        """Rule 8's standing requirement: none borrows from another, so a new
        feature cannot move an existing feature's ceiling."""
        attrs = [spend.ACCOUNT_CAP_ATTR[a] for a in spend.ACCOUNTS]
        assert len(set(attrs)) == len(attrs), attrs

    def test_an_unknown_account_name_raises(self, ledger) -> None:
        with pytest.raises(spend.UnknownAccount):
            spend.cap_for("heatmap")
        with pytest.raises(spend.UnknownAccount):
            _reserve(ledger, "heatmap")
        with pytest.raises(spend.UnknownAccount):
            ledger.spent("heatmap")

    def test_there_is_no_default_account(self, ledger) -> None:
        """A call that does not name a budget has no business spending one."""
        with pytest.raises(TypeError):
            ledger.reserve(model=OPUS, input_tokens=1, max_tokens=1,  # type: ignore[call-arg]
                           key="k", prompt_hash="p")

    def test_the_caps_are_separate_constants(self) -> None:
        """triage and phenotype are both $5.00 today and are still two numbers.
        If one were an alias for the other, moving either would move both."""
        assert (spend.ACCOUNT_CAP_ATTR["triage"]
                != spend.ACCOUNT_CAP_ATTR["phenotype"])


# --------------------------------------------------------------------------
# the caps are genuinely independent
# --------------------------------------------------------------------------

class TestCapsAreIndependent:
    def test_spending_the_phenotype_cap_leaves_triage_at_zero(self, ledger) -> None:
        """The plan's own wording: spend $5 on `phenotype`, `triage` is $0.00."""
        # $5.00 of opus output is 200,000 tokens at $25/MTok. Four calls of
        # 50,000 max_tokens each, committed at their worst case, is exactly the cap.
        for i in range(4):
            r = _reserve(ledger, "phenotype", input_tokens=0, max_tokens=50_000,
                         key=f"gene{i}")
            ledger.commit(r, input_tokens=0, output_tokens=50_000)
        assert ledger.spent("phenotype") == pytest.approx(5.00)
        assert ledger.spent("triage") == 0.0
        assert ledger.calls("triage") == 0

        # And the phenotype account is now full while triage has its whole cap.
        with pytest.raises(spend.CapExceeded):
            _reserve(ledger, "phenotype", input_tokens=0, max_tokens=1)
        r = _reserve(ledger, "triage", input_tokens=0, max_tokens=1)
        assert r.account == "triage"

    def test_spending_the_triage_cap_leaves_phenotype_at_zero(self, ledger) -> None:
        """And vice versa — the symmetric half, which is not implied by the first:
        a shared pool would fail one of these two and pass the other only by
        accident of ordering."""
        for i in range(4):
            r = _reserve(ledger, "triage", input_tokens=0, max_tokens=50_000,
                         key=f"run{i}")
            ledger.commit(r, input_tokens=0, output_tokens=50_000)
        assert ledger.spent("triage") == pytest.approx(5.00)
        assert ledger.spent("phenotype") == 0.0
        assert ledger.calls("phenotype") == 0
        with pytest.raises(spend.CapExceeded):
            _reserve(ledger, "triage", input_tokens=0, max_tokens=1)
        assert _reserve(ledger, "phenotype", input_tokens=0, max_tokens=1)

    def test_moving_one_cap_does_not_move_the_other(self, ledger, monkeypatch) -> None:
        monkeypatch.setattr(config, "PHENOTYPE_LLM_SPEND_CAP_USD", 1.00)
        assert spend.cap_for("phenotype") == 1.00
        assert spend.cap_for("triage") == 5.00

    def test_a_lines_entry_exists_for_each_account_and_names_it(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        ledger.commit(r, input_tokens=6000, output_tokens=600)
        states = ledger.states()
        assert set(states) == set(spend.ACCOUNTS)
        assert states["triage"].line.startswith("triage: $0.00 of $5.00")
        assert "phenotype: $0.0" in states["phenotype"].line
        # Spending on one account moves no other — rule 8's standing rule, and
        # why each has its own counter rather than a share of one.
        assert states["chooser"].spent_usd == 0.0
        assert states["triage"].spent_usd == 0.0


# --------------------------------------------------------------------------
# refusal at the cap, recorded
# --------------------------------------------------------------------------

class TestCapRefusalIsRecorded:
    def test_a_reservation_crossing_the_cap_is_refused_and_recorded(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=0, max_tokens=190_000)
        ledger.commit(r, input_tokens=0, output_tokens=190_000)   # $4.75
        before = ledger.spent("phenotype")

        with pytest.raises(spend.CapExceeded) as e:
            _reserve(ledger, "phenotype", input_tokens=0, max_tokens=20_000)  # $0.50

        # No call was made, so nothing was charged...
        assert ledger.spent("phenotype") == pytest.approx(before)
        # ...and the refusal is nonetheless a line in the file, at $0.
        recs = [x for x in ledger.lines() if x["status"] == "refused_cap"]
        assert len(recs) == 1
        assert recs[0]["usd"] == 0.0
        assert recs[0]["account"] == "phenotype"
        assert "cap" in recs[0]["note"]
        # The exception says where to look.
        assert "spend-ledger" in str(e.value)

    def test_a_refusal_counts_as_a_call_so_the_file_and_the_screen_agree(self, ledger) -> None:
        """Hiding refusals from the call count would make the ledger agree with
        the screen only when nothing went wrong."""
        with pytest.raises(spend.CapExceeded):
            _reserve(ledger, "phenotype", input_tokens=0, max_tokens=10_000_000)
        assert ledger.calls("phenotype") == 1
        assert ledger.spent("phenotype") == 0.0

    def test_the_cap_stops_the_next_call_not_the_current_one(self, ledger) -> None:
        """Reservation is worst-case, so the ceiling is crossed by a refusal, never
        by a completed call."""
        r = _reserve(ledger, "phenotype", input_tokens=0, max_tokens=200_000)  # $5.00
        ledger.commit(r, input_tokens=0, output_tokens=200_000)
        assert ledger.spent("phenotype") == pytest.approx(5.00)
        assert ledger.spent("phenotype") <= spend.cap_for("phenotype")
        with pytest.raises(spend.CapExceeded):
            _reserve(ledger, "phenotype", input_tokens=1, max_tokens=1)

    def test_a_standalone_refusal_needs_no_reservation(self, ledger) -> None:
        ledger.record_refusal(account="phenotype", model=OPUS, key="BRAF",
                              prompt_hash="ph0", reason="no rows to summarise")
        assert ledger.spent("phenotype") == 0.0
        assert ledger.calls("phenotype") == 1


# --------------------------------------------------------------------------
# reserve / commit arithmetic
# --------------------------------------------------------------------------

class TestReserveAndCommit:
    def test_projection_is_worst_case_output(self) -> None:
        # 6,000 input at $5/MTok = $0.030; 1,200 output at $25/MTok = $0.030.
        assert spend.project_usd(OPUS, input_tokens=6000, max_tokens=1200) == \
            pytest.approx(0.06)

    def test_commit_replaces_the_projection_with_the_actual(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        assert ledger.spent("phenotype") == pytest.approx(0.06)   # worst case
        ledger.commit(r, input_tokens=6000, output_tokens=600)
        # $0.030 in + $0.015 out. The plan's ~$0.045 per gene page, from the table.
        assert ledger.spent("phenotype") == pytest.approx(0.045)
        assert ledger.calls("phenotype") == 1, "a commit is not a second call"

    def test_an_uncommitted_reservation_is_counted_as_spent(self, ledger) -> None:
        """The crash case. The reservation is on disk before the request goes out,
        so a process that dies mid-call over-counts rather than under-counts."""
        _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        assert ledger.spent("phenotype") == pytest.approx(0.06)
        recs = ledger.lines()
        assert [x["status"] for x in recs] == ["unknown"]

    def test_a_validation_refusal_still_costs_what_the_call_cost(self, ledger) -> None:
        """A refused summary costs the same as an accepted one (plan §1 item 11)."""
        r = _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        ledger.commit(r, input_tokens=6000, output_tokens=600,
                      status=spend.STATUS_REFUSED_VALIDATION,
                      note="I11: a claim quoted text no cited row contains")
        assert ledger.spent("phenotype") == pytest.approx(0.045)
        assert ledger.lines()[-1]["status"] == "refused_validation"

    def test_an_api_error_commits_what_the_response_reported(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        ledger.commit(r, input_tokens=0, output_tokens=0,
                      status=spend.STATUS_API_ERROR, note="provider 500")
        assert ledger.spent("phenotype") == 0.0

    def test_commit_cannot_be_used_to_write_a_cap_refusal(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=1, max_tokens=1)
        with pytest.raises(spend.SpendError):
            ledger.commit(r, input_tokens=0, output_tokens=0,
                          status=spend.STATUS_REFUSED_CAP)

    def test_cache_reads_are_priced_where_a_price_exists(self, ledger) -> None:
        # 100,000 cache-read tokens at $0.50/MTok = $0.05.
        assert spend.price_usd(OPUS, cache_read_tokens=100_000) == pytest.approx(0.05)

    def test_two_accounts_can_be_spent_in_one_file_without_cross_talk(self, ledger) -> None:
        a = _reserve(ledger, "triage", input_tokens=1000, max_tokens=1000, key="t")
        b = _reserve(ledger, "phenotype", input_tokens=2000, max_tokens=1000, key="p")
        ledger.commit(a, input_tokens=1000, output_tokens=100)
        ledger.commit(b, input_tokens=2000, output_tokens=200)
        assert ledger.spent("triage") == pytest.approx(0.005 + 0.0025)
        assert ledger.spent("phenotype") == pytest.approx(0.010 + 0.005)


# --------------------------------------------------------------------------
# the unpriced model
# --------------------------------------------------------------------------

class TestUnpricedModelIsRefused:
    def test_an_unpriced_model_cannot_be_reserved(self, ledger) -> None:
        with pytest.raises(spend.UnpricedModel) as e:
            _reserve(ledger, "phenotype", model="claude-opus-6")
        assert "claude-opus-5" in str(e.value), "the message names what IS priced"

    def test_an_unpriced_model_is_refused_before_anything_is_written(self, ledger) -> None:
        """An unpriced model is not a cap problem and must not land in the file as
        one — a `refused_cap` line here would make a $5 budget look exhausted."""
        with pytest.raises(spend.UnpricedModel):
            _reserve(ledger, "phenotype", model="gpt-nonexistent")
        assert ledger.lines() == []

    def test_price_is_never_guessed_from_a_neighbouring_model(self) -> None:
        with pytest.raises(spend.UnpricedModel):
            spend.price_usd("claude-opus-5-1", input_tokens=1000)

    def test_an_absent_cache_read_price_is_not_a_price_of_zero(self) -> None:
        """`claude-fable-5-1`'s input/output prices are pinned; its cache-read
        price was never recorded, so cache-read tokens on it are refused."""
        assert spend.price_usd("claude-fable-5-1", input_tokens=1_000_000) == \
            pytest.approx(10.00)
        with pytest.raises(spend.UnpricedModel):
            spend.price_usd("claude-fable-5-1", cache_read_tokens=1000)

    def test_the_pinned_table_has_the_plan_s_numbers(self) -> None:
        t = config.LLM_PRICE_USD_PER_MTOK
        assert t["claude-opus-5"]["input"] == 5.00
        assert t["claude-opus-5"]["output"] == 25.00
        assert t["claude-fable-5-1"]["input"] == 10.00
        assert t["claude-fable-5-1"]["output"] == 50.00


# --------------------------------------------------------------------------
# the file
# --------------------------------------------------------------------------

class TestTheFileIsTheState:
    def test_the_ledger_survives_a_restart(self, tmp_path) -> None:
        p = tmp_path / "spend-ledger.jsonl"
        first = spend.SpendLedger(p)
        r = first.reserve(account="phenotype", model=OPUS, input_tokens=6000,
                          max_tokens=1200, key="BRAF", prompt_hash="ph0")
        first.commit(r, input_tokens=6000, output_tokens=600)
        del first

        # A different object, a different notional process, same file.
        second = spend.SpendLedger(p)
        assert second.spent("phenotype") == pytest.approx(0.045)
        assert second.calls("phenotype") == 1
        assert second.spent("triage") == 0.0

    def test_it_lives_outside_cache_so_a_cache_reset_cannot_zero_it(self) -> None:
        assert config.SPEND_LEDGER_PATH.name == "spend-ledger.jsonl"
        assert config.CACHE_DIR not in config.SPEND_LEDGER_PATH.parents
        assert config.SPEND_LEDGER_PATH.parent == config.RUNS_DIR

    def test_it_is_append_only(self, ledger) -> None:
        r = _reserve(ledger, "phenotype")
        n_after_reserve = len(ledger.path.read_text().splitlines())
        ledger.commit(r, input_tokens=1000, output_tokens=100)
        assert len(ledger.path.read_text().splitlines()) == n_after_reserve + 1

    def test_every_line_carries_the_plan_s_fields(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", key="BRAF")
        ledger.commit(r, input_tokens=1000, output_tokens=100, cache_read_tokens=50)
        for rec in ledger.lines():
            assert set(rec) >= {
                "timestamp_utc", "account", "model", "prompt_hash", "input_tokens",
                "cache_read_tokens", "output_tokens", "usd", "key", "status",
            }
            assert rec["status"] in spend.ALL_STATUSES

    def test_a_missing_file_is_zero_not_an_error(self, tmp_path) -> None:
        led = spend.SpendLedger(tmp_path / "nope" / "spend-ledger.jsonl")
        assert led.spent("phenotype") == 0.0
        assert led.calls("triage") == 0

    def test_an_undecodable_line_raises_rather_than_being_skipped(self, ledger) -> None:
        """Skipping a truncated line would let a reserved $4.90 vanish from the
        total. The one thing this file may never do is under-count."""
        _reserve(ledger, "phenotype", input_tokens=0, max_tokens=190_000)
        with open(ledger.path, "a") as fh:
            fh.write('{"account": "phenotype", "usd": 4.9\n')
        with pytest.raises(spend.SpendError):
            ledger.spent("phenotype")

    def test_a_commit_supersedes_its_reservation_rather_than_adding_to_it(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=0, max_tokens=200_000)
        ledger.commit(r, input_tokens=0, output_tokens=1_000)
        raw = [json.loads(x) for x in ledger.path.read_text().splitlines()]
        assert [x["usd"] for x in raw] == [5.0, 0.025], "both lines are in the file"
        assert ledger.spent("phenotype") == pytest.approx(0.025), "only one is counted"


# --------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------

class TestManifestShowsBothAccounts:
    def test_the_block_names_both_accounts_and_the_price_table(self, ledger) -> None:
        r = _reserve(ledger, "phenotype", input_tokens=6000, max_tokens=1200)
        ledger.commit(r, input_tokens=6000, output_tokens=600)
        block = ledger.manifest_block(model=OPUS, prompt_hash="ph0")
        assert set(block["accounts"]) == set(spend.ACCOUNTS)
        assert block["accounts"]["triage"] == {
            "cap": 5.0, "spent": 0.0, "calls": 0, "remaining": 5.0,
            "purpose": spend.ACCOUNT_PURPOSE["triage"]}
        assert block["accounts"]["phenotype"]["spent"] == pytest.approx(0.045)
        assert block["accounts"]["phenotype"]["calls"] == 1
        assert block["model"] == OPUS
        assert block["prompt_hash"] == "ph0"
        assert block["price_table"]["claude-opus-5"]["output"] == 25.00
        assert block["price_table_cached_utc"] == "2026-06-24"

    def test_both_lines_are_present_even_when_nothing_was_spent(self, ledger) -> None:
        block = ledger.manifest_block()
        assert block["lines"] == ["triage: $0.00 of $5.00 spent over 0 calls",
                                 "phenotype: $0.00 of $5.00 spent over 0 calls",
                                 "chooser: $0.00 of $2.00 spent over 0 calls",
                                 "region: $0.00 of $1.00 spent over 0 calls"]

    def test_a_built_manifest_carries_the_two_lines(self, monkeypatch, tmp_path) -> None:
        """The end the artefact is read from: `manifest.build` through to the JSON
        a screenshot is audited against."""
        monkeypatch.setattr(config, "SPEND_LEDGER_PATH", tmp_path / "spend-ledger.jsonl")
        led = spend.SpendLedger()
        r = led.reserve(account="phenotype", model=OPUS, input_tokens=6000,
                        max_tokens=1200, key="BRAF", prompt_hash="ph0")
        led.commit(r, input_tokens=6000, output_tokens=600)

        m = manifest_mod.build(
            run_id="t", region="Lung", input_row_count=0, rendered_count=0,
            ledger_count=0, gene_order=[], invariants=InvariantReport(),
            contracts=[], ledger=[], timings={},
            llm=spend.default_ledger().manifest_block(model=OPUS, prompt_hash="ph0"),
        )
        out = json.loads(json.dumps(m.as_dict(), default=str))
        assert out["llm"]["accounts"]["triage"]["spent"] == 0.0
        assert out["llm"]["accounts"]["phenotype"]["spent"] == pytest.approx(0.045)
        assert out["llm"]["price_table"]["claude-opus-5"]["input"] == 5.00

    def test_no_manifest_reports_a_hard_coded_zero_any_more(self, monkeypatch,
                                                            tmp_path) -> None:
        """The defect stage 0 exists to remove: the old block was the literal
        `{"attempted": False, "spend_usd": 0.0}` with no counter behind it."""
        monkeypatch.setattr(config, "SPEND_LEDGER_PATH", tmp_path / "spend-ledger.jsonl")
        block = spend.manifest_block()
        assert "spend_usd" not in block
        assert "accounts" in block and "ledger_path" in block
