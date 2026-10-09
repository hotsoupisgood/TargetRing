"""Choosing a contrast autonomously, and the audit trail that makes it legible.

Puck's decision of 2026-10-09 reverses this build's earlier refusal to name a
default contrast. `autopick.py`'s docstring says what is and is not reversed;
these are the guards.

The failure mode is the whole point: a wrong contrast produces a complete,
internally consistent, entirely wrong figure, with every invariant passing,
because nothing downstream can tell the gene set came from the wrong
experiment. Picking by hand, a reader at least saw the rows they chose between.
Picking automatically removes that, so the pick has to carry what the reader
would have seen.

No network and no analysis (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import pytest

from targetring import atlas_index as idx
from targetring import autopick
from targetring.adapters.ols import DiseaseRef
from targetring.atlas_index import AtlasIndex
from targetring.config import REPO_ROOT

CROHNS_INDEX = REPO_ROOT / "fixtures" / "atlas" / "index" / "crohns-index.json"


def row(**kw):
    base = dict(accession="E-TEST-1", contrast_id="g2_g1", n_ref=10, n_test=10,
                min_n=10, ref_label="normal", test_label="disease",
                tissue=["ileum"], comparison="case_vs_unaffected",
                flag_keys=[], drawable=True)
    base.update(kw)
    return base


@pytest.fixture(scope="module")
def crohns_rows():
    index = AtlasIndex.load(CROHNS_INDEX)
    ref = DiseaseRef(label="Crohn disease", current_id="MONDO_0005011",
                     atlas_ids=("MONDO_0005011", "EFO_0000384"))
    return idx.picker_payload(index, ref)["rows"]


class TestOnlyEligibleRowsAreChosenAmong:

    def test_a_row_this_app_cannot_draw_is_excluded_with_a_reason(self) -> None:
        cands, excluded = autopick.candidates_from_rows(
            [row(drawable=False, accession="E-ARRAY-1")])
        assert not cands
        assert excluded[0][1] == "not_drawable"

    def test_a_within_disease_row_is_excluded_with_a_reason(self) -> None:
        cands, excluded = autopick.candidates_from_rows(
            [row(comparison="within_one_disease")])
        assert not cands
        assert excluded[0][1] == "not_case_control"

    def test_nothing_is_dropped_silently(self) -> None:
        """An automatic picker that considered 17 of 138 rows without saying so
        would make the most consequential part of the choice invisible."""
        rows = [row(), row(drawable=False, contrast_id="g3_g1"),
                row(comparison="within_one_disease", contrast_id="g4_g1")]
        cands, excluded = autopick.candidates_from_rows(rows)
        assert len(cands) + len(excluded) == len(rows)

    def test_every_exclusion_reason_has_prose(self) -> None:
        for key in ("not_drawable", "not_case_control"):
            assert len(autopick.EXCLUSION_REASONS[key]) > 40


class TestTheChoiceIsVerifiedAgainstWhatItWasOffered:

    def test_a_chooser_cannot_return_a_contrast_nobody_offered(self) -> None:
        """The same guard `/api/analyse` applies to gene ids. A chooser that can
        return something outside its candidate set can invent an accession."""
        outsider = autopick.Candidate(
            accession="E-INVENTED-9", contrast_id="g9_g9", n_ref=1, n_test=1,
            min_n=1, ref_label="", test_label="", tissue=(),
            comparison="case_vs_unaffected", flag_keys=())
        with pytest.raises(ValueError, match="not one of"):
            autopick.Pick(chooser="rogue", stated_rule="r", chosen=outsider,
                          reason="", considered=())

    def test_choosing_nothing_is_allowed_and_is_not_an_error(self) -> None:
        p = autopick.Pick(chooser="c", stated_rule="r", chosen=None,
                          reason="nothing eligible", considered=())
        assert p.chosen is None


class TestTheRuleChooser:

    def test_it_takes_the_most_samples_on_the_smaller_side(self) -> None:
        rows = [row(contrast_id="g2_g1", n_ref=13, n_test=61, min_n=13),
                row(contrast_id="g3_g1", n_ref=61, n_test=43, min_n=43)]
        p = autopick.pick(rows)
        assert p.chosen.contrast_id == "g3_g1"
        assert "43 samples on its smaller side" in p.reason

    def test_it_is_deterministic_on_a_tie(self) -> None:
        rows = [row(accession="E-B", min_n=10), row(accession="E-A", min_n=10)]
        assert autopick.pick(rows).chosen.accession == \
            autopick.pick(list(reversed(rows))).chosen.accession

    def test_it_refuses_rather_than_picking_the_least_bad_row(self) -> None:
        """An empty candidate list must never resolve to a winner — that is how
        an automatic picker starts drawing contrasts a human would reject."""
        p = autopick.pick([row(drawable=False)])
        assert p.chosen is None
        assert "no contrast" in p.reason
        assert len(p.excluded) == 1

    def test_the_rule_is_stated_as_a_sentence_not_a_function_name(self) -> None:
        p = autopick.pick([row()])
        assert len(p.stated_rule) > 60
        assert "smaller side" in p.stated_rule

    def test_it_combines_nothing(self) -> None:
        """min(n_ref, n_test) is one number from one source. A chooser scoring
        contrasts on several axes would be the machinery rule 7 removed,
        rebuilt one level upstream."""
        import inspect
        # The EXECUTABLE method, not the module text: the class docstring
        # contains "no weights" precisely because there are none, and a
        # whole-file substring check reads that denial as the thing it denies.
        body = inspect.getsource(autopick.RuleChooser.choose)
        for banned in ("weight", "score", "* 0.", "sum(", "+ c."):
            assert banned not in body, banned
        assert "min_n" in body, "it decides on exactly one field"


class TestTheAuditTrail:

    def test_the_pick_carries_the_candidates_and_the_exclusions(self, crohns_rows
                                                                 ) -> None:
        p = autopick.pick(crohns_rows)
        d = p.as_dict()
        assert d["n_considered"] == len(p.considered)
        assert len(d["excluded"]) == len(p.excluded)
        assert d["stated_rule"] and d["reason"]
        assert all(e["excluded_reason"] for e in d["excluded"])

    def test_the_control_side_is_characterised_when_it_is_not_healthy(self
                                                                      ) -> None:
        """"Control" in Atlas is frequently not a healthy volunteer, and which
        kind it is changes what every log2FC on the figure means. A reader
        choosing by hand reads the label; a reader handed a pick does not."""
        c = autopick.Candidate(
            accession="E-GEOD-101794", contrast_id="g2_g1", n_ref=13,
            n_test=61, min_n=13,
            ref_label="A1a Paris classification; non inflammatory bowel disease control",
            test_label="Crohn's disease", tissue=("ileum",),
            comparison="case_vs_unaffected", flag_keys=())
        assert "not healthy volunteers" in autopick.control_caveat(c)

    def test_a_plainly_normal_control_gets_no_caveat(self) -> None:
        c = autopick.Candidate(
            accession="E", contrast_id="g2_g1", n_ref=1, n_test=1, min_n=1,
            ref_label="normal", test_label="disease", tissue=(),
            comparison="case_vs_unaffected", flag_keys=())
        assert autopick.control_caveat(c) == ""

    def test_on_the_real_index_it_considers_only_the_drawable_case_controls(
            self, crohns_rows) -> None:
        p = autopick.pick(crohns_rows)
        assert len(p.considered) == 17
        assert len(p.excluded) == len(crohns_rows) - 17
        assert all(c.comparison == "case_vs_unaffected" for c in p.considered)


class TestTheRoute:
    """`/api/autopick` is a separate action, not a field on the listing."""

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from targetring.app import app
        return TestClient(app)

    def test_it_chooses_and_shows_its_working(self, client) -> None:
        r = client.get("/api/autopick", params={"disease": "MONDO_0005011"})
        assert r.status_code == 200
        d = r.json()
        assert d["chosen"]["accession"] and d["chosen"]["contrast_id"]
        assert d["stated_rule"] and d["reason"]
        assert d["n_considered"] >= 1
        assert d["excluded"], "the rows it did not consider must be named"

    def test_a_disease_is_required(self, client) -> None:
        assert client.get("/api/autopick").status_code == 400

    def test_an_unresolvable_disease_is_refused_not_guessed_at(self, client) -> None:
        r = client.get("/api/autopick", params={"disease": "NOT_A_TERM_12345"})
        assert r.status_code == 422
        assert r.json()["error"] == "disease_not_resolvable"

    def test_the_listing_is_still_neutral(self, client) -> None:
        """The refusal this reversal does NOT touch: `/api/contrasts` names no
        default. The pick is a separate request a caller makes on purpose."""
        r = client.get("/api/contrasts", params={"disease": "MONDO_0005011"})
        assert r.status_code == 200
        body = r.json()
        for banned in ("selected", "recommended", "default", "best", "chosen"):
            assert banned not in body, banned
            assert banned not in body["rows"][0], banned


class FakeCache:
    """The `Cache` surface the choosers use: get/put on (source, key)."""

    def __init__(self):
        self.store = {}

    def get(self, source, key):
        return self.store.get((source, key))

    def put(self, source, key, body):
        self.store[(source, key)] = body


class FakeClient:
    """A chooser client with canned outcomes. Never spends, never calls out."""

    model = "claude-opus-5"

    def __init__(self, outcome):
        self.outcome = outcome
        self.seen = None
        self.calls = 0

    def complete(self, cands, *, max_tokens):
        self.seen = list(cands)
        self.calls += 1
        return self.outcome


class TestTheModelChooser:
    """Rule 8's third paid call, permitted 2026-10-09. The guards are about
    what it may return and what happens when it does not return it."""

    @staticmethod
    def two_rows():
        return [row(contrast_id="g2_g1", min_n=13, n_ref=13, n_test=61),
                row(contrast_id="g3_g1", min_n=43, n_ref=61, n_test=43)]

    def ok(self, key, reason="because"):
        from targetring.chooser_llm import ChoiceOutcome
        return ChoiceOutcome(ok=True, key=key, reason=reason,
                             model="claude-opus-5", input_tokens=100,
                             output_tokens=20)

    def test_it_can_choose_a_contrast_the_rule_would_not(self) -> None:
        """The whole point of paying: the rule takes the largest smaller side;
        a model may weigh tissue or what the control actually is."""
        from targetring.chooser_llm import ModelChooser
        ch = ModelChooser(client=FakeClient(self.ok("E-TEST-1 g2_g1")))
        p = autopick.pick(self.two_rows(), ch)
        assert p.chosen.contrast_id == "g2_g1"
        assert p.chooser == "model"
        assert autopick.pick(self.two_rows()).chosen.contrast_id == "g3_g1"

    def test_it_is_only_shown_public_atlas_metadata(self) -> None:
        """Rule 10. Accessions, labels, assay counts and tissue — nothing Puck
        typed, no run id, no region, no gene."""
        from targetring import chooser_llm
        content = chooser_llm.user_content(
            autopick.candidates_from_rows(self.two_rows())[0])
        import json as _json
        for item in _json.loads(content):
            assert set(item) == {"key", "test", "n_test", "control",
                                 "n_control", "tissue"}

    def test_a_key_outside_the_list_falls_back_and_says_so(self) -> None:
        """A model naming something it was not offered is foreseeable, so it is
        handled rather than raised — but never silently."""
        from targetring.chooser_llm import ModelChooser
        ch = ModelChooser(client=FakeClient(self.ok("E-INVENTED-9 g9_g9")))
        p = autopick.pick(self.two_rows(), ch)
        assert p.chosen is not None
        assert p.chooser == "largest-smaller-side"
        assert "not one of" in p.fell_back_from

    def test_a_failed_call_falls_back_and_names_the_error(self) -> None:
        from targetring.chooser_llm import ChoiceOutcome, ModelChooser
        ch = ModelChooser(client=FakeClient(
            ChoiceOutcome(ok=False, error="no credentials")))
        p = autopick.pick(self.two_rows(), ch)
        assert p.chooser == "largest-smaller-side"
        assert "no credentials" in p.fell_back_from

    def test_the_fallback_never_claims_the_model_chose(self) -> None:
        """The attribution that makes a fallback permissible at all. Telling a
        reader a model chose when the rule did is a wrong claim about the
        figure's own provenance."""
        from targetring.chooser_llm import ChoiceOutcome, ModelChooser
        ch = ModelChooser(client=FakeClient(ChoiceOutcome(ok=False, error="x")))
        p = autopick.pick(self.two_rows(), ch)
        assert p.chooser != "model"
        assert "model" in p.fell_back_from
        assert p.stated_rule == autopick.RuleChooser().stated_rule

    def test_an_empty_candidate_set_is_not_retried(self) -> None:
        """A genuine refusal has nothing to fall back to, and inventing one is
        the failure the whole module guards against."""
        from targetring.chooser_llm import ModelChooser
        ch = ModelChooser(client=FakeClient(self.ok("anything")))
        p = autopick.pick([row(drawable=False)], ch)
        assert p.chosen is None
        assert p.fell_back_from == ""

    def test_the_cap_refuses_before_the_call_is_made(self, tmp_path,
                                                     monkeypatch) -> None:
        """Rule 8 as amended: a cap is an approval gate. It stops, names the
        number, and the page still works."""
        from targetring import config as cfg
        from targetring import spend as spend_mod
        from targetring.chooser_llm import ModelChooser
        monkeypatch.setattr(cfg, "SPEND_LEDGER_PATH", tmp_path / "s.jsonl")
        monkeypatch.setattr(cfg, "CHOOSER_LLM_SPEND_CAP_USD", 0.0)
        client = FakeClient(self.ok("E-TEST-1 g2_g1"))
        ch = ModelChooser(client=client,
                          ledger=spend_mod.SpendLedger(tmp_path / "s.jsonl"))
        p = autopick.pick(self.two_rows(), ch)
        assert client.seen is None, "no call may be made once the cap refuses"
        assert p.chooser == "largest-smaller-side"
        assert "$" in p.fell_back_from

    def test_it_returns_one_choice_and_no_scores(self) -> None:
        """A scored list of contrasts is the machinery rule 7 removed, rebuilt
        one level upstream, and is the thing a reader starts sorting by."""
        from targetring.chooser_llm import ContrastChoice
        assert set(ContrastChoice.model_fields) == {"key", "reason"}

    def test_an_empty_key_reads_as_the_model_declining_not_inventing(self) -> None:
        """The prompt invites an empty key when nothing suits. That is a
        different fact from naming a row that is not on the list, and reporting
        the first as the second would hide a judgement worth reading."""
        from targetring.chooser_llm import ChoiceOutcome, ModelChooser
        ch = ModelChooser(client=FakeClient(ChoiceOutcome(
            ok=True, key="", reason="all three are whole blood", model="m")))
        p = autopick.pick(self.two_rows(), ch)
        assert p.chooser == "largest-smaller-side"
        assert "found none" in p.fell_back_from
        assert "whole blood" in p.fell_back_from

    def test_the_token_budget_has_room_for_the_sentence_it_asks_for(self) -> None:
        """Found live on 2026-10-09: at max_tokens=400 the model ran past the
        budget mid-sentence on MONDO_0005301, the JSON was cut off and
        `messages.parse` raised "EOF while parsing a string". It fell back
        correctly, but the call was paid for and produced nothing."""
        from targetring.chooser_llm import ModelChooser, SYSTEM_PROMPT
        assert ModelChooser(client=None).max_tokens >= 700
        # and the prompt bounds the sentence, so budget and ask agree
        assert "35 words" in SYSTEM_PROMPT
        assert "truncated" in SYSTEM_PROMPT


class TestTheLedgerRecordsWhatActuallyHappened:
    """`phenotype.py` is the pattern: an explicit `status` and the cache-read
    tokens. Committing with the defaults logged every failed call as `ok` and
    billed no cache reads — a ledger that reports a failure as a success is the
    accounting version of a plausible wrong figure."""

    def test_a_failed_call_is_not_recorded_as_ok(self, tmp_path, monkeypatch) -> None:
        from targetring import config as cfg, spend as spend_mod
        from targetring.chooser_llm import ChoiceOutcome, ModelChooser
        monkeypatch.setattr(cfg, "SPEND_LEDGER_PATH", tmp_path / "s.jsonl")
        led = spend_mod.SpendLedger(tmp_path / "s.jsonl")
        ch = ModelChooser(client=FakeClient(
            ChoiceOutcome(ok=False, error="boom", model="claude-opus-5")),
            ledger=led, disease="x")
        autopick.pick(self.two_rows(), ch)
        statuses = [l["status"] for l in led.lines()
                    if l["status"] in spend_mod.TERMINAL_STATUSES]
        assert spend_mod.STATUS_API_ERROR in statuses, statuses
        assert spend_mod.STATUS_OK not in statuses

    def test_a_provider_refusal_is_recorded_as_a_validation_refusal(
            self, tmp_path, monkeypatch) -> None:
        from targetring import config as cfg, spend as spend_mod
        from targetring.chooser_llm import ChoiceOutcome, ModelChooser
        monkeypatch.setattr(cfg, "SPEND_LEDGER_PATH", tmp_path / "s.jsonl")
        led = spend_mod.SpendLedger(tmp_path / "s.jsonl")
        ch = ModelChooser(client=FakeClient(ChoiceOutcome(
            ok=False, stop_reason="refusal", error="refused",
            model="claude-opus-5")), ledger=led, disease="x")
        autopick.pick(self.two_rows(), ch)
        assert spend_mod.STATUS_REFUSED_VALIDATION in [
            l["status"] for l in led.lines()]

    def test_the_ledger_key_names_the_disease(self) -> None:
        """Keyed on the candidate count alone, chooser spend could not be traced
        back to what it was spent on."""
        from targetring.chooser_llm import ModelChooser
        import inspect
        src = inspect.getsource(ModelChooser.choose)
        assert "self.disease" in src

    @staticmethod
    def two_rows():
        return [row(contrast_id="g2_g1", min_n=13),
                row(contrast_id="g3_g1", min_n=43)]


class TestTheChooserIsCached:
    """Without it the picker made a paid, non-deterministic call on EVERY
    disease interaction: ~36 of them exhaust the $2 cap, and "Chosen for you"
    could differ between two identical requests."""

    @staticmethod
    def two_rows():
        return [row(contrast_id="g2_g1", min_n=13),
                row(contrast_id="g3_g1", min_n=43)]

    def ok(self, key):
        from targetring.chooser_llm import ChoiceOutcome
        return ChoiceOutcome(ok=True, key=key, reason="because",
                             model="claude-opus-5")

    def test_the_second_ask_does_not_call_the_model(self) -> None:
        from targetring.chooser_llm import ModelChooser
        client = FakeClient(self.ok("E-TEST-1 g2_g1"))
        ch = ModelChooser(client=client, cache=FakeCache(), disease="Crohn")
        autopick.pick(self.two_rows(), ch)
        autopick.pick(self.two_rows(), ch)
        assert client.calls == 1

    def test_a_cached_answer_says_it_is_remembered(self) -> None:
        from targetring.chooser_llm import ModelChooser
        ch = ModelChooser(client=FakeClient(self.ok("E-TEST-1 g2_g1")),
                          cache=FakeCache(), disease="Crohn")
        autopick.pick(self.two_rows(), ch)
        again = autopick.pick(self.two_rows(), ch)
        assert "remembered from an earlier run" in again.reason

    def test_a_changed_candidate_set_invalidates(self) -> None:
        """The index is rebuilt; a contrast appearing or disappearing can change
        the right answer."""
        from targetring import chooser_llm
        cands = autopick.candidates_from_rows(self.two_rows())[0]
        more = autopick.candidates_from_rows(
            self.two_rows() + [row(contrast_id="g9_g1", min_n=5)])[0]
        assert chooser_llm.cache_key("x", cands) != chooser_llm.cache_key("x", more)

    def test_the_cache_source_has_a_schema_version(self) -> None:
        from targetring import config
        assert config.SCHEMA_VERSIONS["chooser_llm"]


class TestTheTieBreakMatchesItsStatedRule:

    @staticmethod
    def rows3():
        return [row(accession="E-C", min_n=12), row(accession="E-A", min_n=12),
                row(accession="E-B", min_n=12)]

    def test_a_tie_goes_to_the_first_accession_not_the_last(self) -> None:
        """The rule says "ties broken by accession then contrast id"; `max` over
        a tuple took the highest, i.e. reverse-alphabetical."""
        assert autopick.pick(self.rows3()).chosen.accession == "E-A"

    def test_a_tie_does_not_claim_a_better_runner_up(self) -> None:
        """It read "the most of 3 candidate(s); the next best has 12" — the
        next best WAS 12, because it was reading the whole list."""
        p = autopick.pick(self.rows3())
        assert "the next best has" not in p.reason
        assert "3 candidates tie at 12" in p.reason

    def test_a_genuine_runner_up_is_still_named(self) -> None:
        p = autopick.pick([row(accession="E-C", min_n=43),
                           row(accession="E-A", min_n=12)])
        assert "the next best has 12" in p.reason
