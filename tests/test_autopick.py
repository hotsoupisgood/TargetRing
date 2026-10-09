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
