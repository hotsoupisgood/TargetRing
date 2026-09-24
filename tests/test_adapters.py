"""Adapter contracts.

The offline tests pin the reductions and the four-state logic against
hand-built records. The live tests are the startup contract test QA B1 demands:
they issue the exact production query and fail loudly on any non-200 or any
`errors` key, rather than letting a 400 render as an empty ring.
"""

from __future__ import annotations

import pytest

from targetring.adapters.chembl import ChEMBLRecord, mechanism_summary
from targetring.adapters.gtex import GTExRecord, specificity_cell
from targetring.adapters.literature import (
    LiteratureRecord,
    QueryRejected,
    literature_cell,
    pubtator_gene_query,
    validate_query,
)
from targetring.adapters.opentargets import (
    CLINICAL_STAGE_ORDINAL,
    OTRecord,
    clinical_activity_cell,
    clinical_phase_cell,
    druggability_cell,
    rodent_cell,
    safety_cell,
    severe_liabilities,
)


def ot(**kw) -> OTRecord:
    kw.setdefault("ensembl_id", "ENSG00000157764")
    kw.setdefault("found", True)
    return OTRecord(**kw)


class TestMissingIsNotLow:
    """AGENTS.md rule 1. The whole app is downstream of this."""

    def test_absent_from_batch_is_no_data_not_zero(self) -> None:
        rec = OTRecord("ENSG00000999999", found=False)
        for cell in (druggability_cell(rec), safety_cell(rec),
                     clinical_phase_cell(rec), rodent_cell(rec)):
            assert cell.state == "no_data"
            assert cell.value is None

    def test_a_genuinely_unscored_key_yields_no_data_not_a_shorter_mean(self) -> None:
        """Superseded by COUNCIL V4, and strengthened.

        v1 asserted that a gene with one of four keys got `mean(1.0) = 1.0` and
        a "1/4 keys" detail — i.e. a shorter mean. The council's V4 showed that
        a shorter mean *is* imputation: dropping an absent key from a mean is
        arithmetically identical to imputing it at the mean of the present ones.

        The rule this test protects is unchanged — an unscored key is never
        averaged in as zero — but the honest answer is now `no_data`, not a
        number computed on a different denominator from every other gene's.
        """
        full = ot(prioritisation={"hasLigand": 1.0, "hasPocket": 1.0,
                                  "hasSmallMoleculeBinder": 1.0,
                                  "hasHighQualityChemicalProbes": 1.0})
        partial = ot(prioritisation={"hasLigand": 1.0})
        assert druggability_cell(full).value == 1.0

        cell = druggability_cell(partial)
        assert cell.state == "no_data" and cell.value is None
        assert "hasPocket" in cell.detail and "hasSmallMoleculeBinder" in cell.detail
        # Specifically: not 0.25 (zero-filled) and not 1.0 (shorter mean).
        assert "denominator would encode the answer" in cell.detail

    def test_no_keys_at_all_is_no_data(self) -> None:
        assert druggability_cell(ot(prioritisation={})).state == "no_data"


def _row(stage, *disease_ids, drug="DRUG"):
    """One drugAndClinicalCandidates row with its indications."""
    return {
        "maxClinicalStage": stage,
        "drug": {"id": "CHEMBL1", "name": drug},
        "diseases": [{"disease": {"id": d, "name": d.lower()}} for d in disease_ids],
        "clinicalReports": [],
    }


class TestClinicalPhase:
    def test_string_enum_is_never_parsed_as_int(self) -> None:
        """COUNCIL M1: parseInt('PHASE_1_2') is the bug this table prevents."""
        assert CLINICAL_STAGE_ORDINAL["PHASE_1_2"] == 2
        assert CLINICAL_STAGE_ORDINAL["APPROVAL"] == 5
        assert "PHASE_1_2".isdigit() is False

    def test_meaningful_zero(self) -> None:
        """COUNCIL D5: a target record with 0 candidates is genuinely undrugged."""
        c = clinical_phase_cell(ot(drug_count=0))
        assert c.state == "value" and c.value == 0.0
        assert "genuinely" in c.detail or "0 drug" in c.detail

    def test_max_stage_wins_among_attributable_rows(self) -> None:
        """Superseded by COUNCIL V1: the max is now taken over attributable rows
        only. Given attribution, the ordinal behaves exactly as before."""
        rec = ot(drug_count=3, drug_rows=[
            _row("PHASE_1", "MONDO_1"), _row("APPROVAL", "MONDO_1"),
            _row("PHASE_2", "MONDO_1"),
        ])
        rec.associations = {"MONDO_1": {"genetic_association": 0.8}}
        assert clinical_phase_cell(rec).value == 5.0

    def test_unmapped_stage_does_not_become_zero(self) -> None:
        rec = ot(drug_count=2, drug_rows=[
            {"maxClinicalStage": None, "diseases": []},
            {"maxClinicalStage": "SOMETHING_NEW", "diseases": []},
        ])
        rec.associations = {}
        c = clinical_phase_cell(rec)
        # v1 returned value(1.0) here, which quietly absorbed any new enum value
        # Open Targets might introduce. An unmapped stage is a fact we do not
        # have, not a low one — and it must not become zero either.
        assert c.state == "no_data" and c.value is None
        assert "SOMETHING_NEW" in c.detail


class TestClinicalActivity:
    def _rows(self, *reports):
        return [{"clinicalReports": list(reports)}]

    def test_only_aact_rows_carry_status(self) -> None:
        """COUNCIL D1 gap (ii): TTD/EMA rows have None status and would read as
        inactive."""
        rec = ot(drug_count=1, drug_rows=self._rows(
            {"source": "TTD", "trialOverallStatus": None},
            {"source": "EMA Human Drugs", "trialOverallStatus": None},
        ))
        assert clinical_activity_cell(rec).state == "no_data"

    def test_active_outranks_historical_for_cause_stops(self) -> None:
        """Precedence is this build's call: COUNCIL D4 lists three overlapping
        states without ordering, and for-cause-first labels BRAF 'stopped'
        while 10 of its 18 agents recruit."""
        rec = ot(drug_count=1, drug_rows=self._rows(
            {"source": "AACT", "trialOverallStatus": "RECRUITING"},
            {"source": "AACT", "trialOverallStatus": "TERMINATED",
             "trialStopReasonCategories": ["Safety_Sideeffects"]},
        ))
        c = clinical_activity_cell(rec)
        assert c.value == 1.0
        assert "stopped for cause" in c.detail  # not hidden, just not the headline

    def test_stopped_for_cause_when_nothing_active(self) -> None:
        rec = ot(drug_count=1, drug_rows=self._rows(
            {"source": "AACT", "trialOverallStatus": "TERMINATED",
             "trialStopReasonCategories": ["Negative"]},
        ))
        assert clinical_activity_cell(rec).value == -1.0

    def test_enrollment_failure_is_not_for_cause(self) -> None:
        """COUNCIL D4: only ~13% of stop events say anything about the target."""
        rec = ot(drug_count=1, drug_rows=self._rows(
            {"source": "AACT", "trialOverallStatus": "TERMINATED",
             "trialStopReasonCategories": ["Insufficient_Enrollment",
                                           "Business_Administrative"]},
        ))
        assert clinical_activity_cell(rec).value == 0.0  # dormant, not stopped

    def test_no_drugs_is_not_applicable_not_no_data(self) -> None:
        assert clinical_activity_cell(ot(drug_count=0)).state == "not_applicable"


class TestRodent:
    """COUNCIL V9 reduced this ring to three states. The council explicitly
    endorsed v1's handling of the informative negative and it is unchanged;
    what changed is that the *count* no longer reaches the ring."""

    def test_no_abnormal_phenotype_is_excluded_before_the_test(self) -> None:
        """QA S4: counting rows is not counting abnormal phenotypes. A gene with
        one 'no abnormal phenotype' record must not outrank a gene with none."""
        rec = ot(mouse_phenotypes=[
            {"modelPhenotypeLabel": "no abnormal phenotype detected"},
            {"modelPhenotypeLabel": "abnormal heart morphology"},
        ])
        c = rodent_cell(rec)
        assert c.value == 1.0
        assert "1 abnormal term(s) of 2 record(s)" in c.detail
        assert c.raw["n_abnormal"] == 1 and c.raw["n_rows"] == 2

    def test_phenotyped_but_normal_is_a_measured_zero(self) -> None:
        rec = ot(mouse_phenotypes=[{"modelPhenotypeLabel": "no abnormal phenotype detected"}])
        c = rodent_cell(rec)
        assert c.state == "value" and c.value == 0.0
        assert c.raw["state"] == "phenotyped_normal"

    def test_never_phenotyped_is_no_data(self) -> None:
        c = rodent_cell(ot(mouse_phenotypes=[]))
        assert c.state == "no_data"
        assert "not the same as 'no phenotype'" in c.detail

    def test_the_count_no_longer_reaches_the_ring(self) -> None:
        """COUNCIL V9. v1 put the count on the ring, so TP53 scored 249 -- the
        maximum -- and was *rewarded* for having the most catastrophic,
        pleiotropic knockout in the set, on a direction nothing justified."""
        tp53 = ot(mouse_phenotypes=[{"modelPhenotypeLabel": f"abnormal {i}"}
                                    for i in range(249)])
        one = ot(mouse_phenotypes=[{"modelPhenotypeLabel": "abnormal heart morphology"}])
        assert rodent_cell(tp53).value == rodent_cell(one).value == 1.0
        # The count is still available, in the drawer and the CSV detail.
        assert rodent_cell(tp53).raw["n_abnormal"] == 249

    def test_the_denominator_is_no_longer_thrown_away(self) -> None:
        """v1: 1 abnormal of 1 record and 1 abnormal of 50 were both 1.0, with
        no way to tell them apart. Both are still 'phenotyped, abnormal', but
        the denominator now travels with the cell."""
        thin = ot(mouse_phenotypes=[{"modelPhenotypeLabel": "abnormal x"}])
        thick = ot(mouse_phenotypes=(
            [{"modelPhenotypeLabel": "abnormal x"}]
            + [{"modelPhenotypeLabel": "no abnormal phenotype detected"}] * 49))
        assert rodent_cell(thin).raw["n_rows"] == 1
        assert rodent_cell(thick).raw["n_rows"] == 50

    def test_the_ring_is_in_no_score(self) -> None:
        from targetring.rank import DEFAULT_WEIGHTS, EXCLUDED_FROM_SCORE
        from targetring.run import RANKED_RINGS
        assert "rodent" in EXCLUDED_FROM_SCORE
        assert "rodent" not in DEFAULT_WEIGHTS and "rodent" not in RANKED_RINGS


class TestV14PipelineSummaryReadsStraight:
    """COUNCIL V14: EPHA2 read "3 agents, 3 active, 3 stopped for cause" -- three
    agents both running and killed. The two counts are not alternatives."""

    def test_the_counts_are_labelled_as_overlapping(self) -> None:
        from targetring.adapters.opentargets import pipeline_summary
        rec = ot(drug_count=3, drug_rows=[
            {"maxClinicalStage": "APPROVAL", "drug": {"name": f"D{i}"}, "diseases": [],
             "clinicalReports": [
                 {"source": "AACT", "trialOverallStatus": "RECRUITING"},
                 {"source": "AACT", "trialOverallStatus": "TERMINATED",
                  "trialStopReasonCategories": ["Safety_Sideeffects"]}]}
            for i in range(3)])
        line = pipeline_summary(rec)
        assert "3 agents" in line
        assert "3 with an active trial" in line
        assert "3 with ≥1 trial stopped for cause" in line
        assert ", 3 active, 3 stopped for cause" not in line

    def test_it_reports_how_many_indications_are_attributable(self) -> None:
        from targetring.adapters.opentargets import pipeline_summary
        rec = ot(drug_count=2, drug_rows=[
            _row("APPROVAL", "MONDO_OWN"), _row("APPROVAL", "MONDO_OTHER")])
        rec.associations = {"MONDO_OWN": {"genetic_association": 0.9},
                            "MONDO_OTHER": {"clinical": 0.9}}
        assert "1 indicated for a disease attributable to this target" \
            in pipeline_summary(rec)


class TestV7ExpressionFloor:
    """COUNCIL V7: GFAP (astrocyte-restricted, 0.216 TPM in liver) scored 0.270
    and ACTB (housekeeping, 760.4 TPM in liver) scored 0.344 — adjacent cells,
    near-identical colour, opposite biology."""

    def _rec(self, region_tpm, others):
        return GTExRecord("ENSG1.1", {"Liver": region_tpm,
                                      **{f"T{i}": v for i, v in enumerate(others)}})

    def test_a_gene_not_expressed_here_is_no_data_not_a_ratio(self) -> None:
        from targetring.adapters.gtex import REGION_TPM_FLOOR
        gfap = self._rec(0.216, [0.5, 0.8, 1.0, 1.2])
        c = specificity_cell(gfap, "Liver")
        assert c.state == "no_data" and c.value is None
        assert "below the" in c.detail and "floor" in c.detail
        assert REGION_TPM_FLOOR == 1.0

    def test_a_housekeeping_gene_still_gets_its_ratio(self) -> None:
        actb = self._rec(760.4, [1000.0, 2000.0, 2500.0, 3000.0])
        c = specificity_cell(actb, "Liver")
        assert c.state == "value"
        assert 0.0 < c.value < 1.0

    def test_the_absolute_tpm_still_travels(self) -> None:
        gfap = self._rec(0.216, [0.5, 0.8, 1.0, 1.2])
        assert "0.216 TPM" in specificity_cell(gfap, "Liver").detail


class TestGTEx:
    def test_wrong_version_absence_is_no_data_with_the_reason_spelled_out(self) -> None:
        c = specificity_cell(GTExRecord("ENSG00000157764.12", {}), "Lung")
        assert c.state == "no_data"
        assert "not a version mismatch" in c.detail

    def test_ratio(self) -> None:
        rec = GTExRecord("X.1", {"Lung": 20.0, "Liver": 10.0, "Brain": 5.0})
        c = specificity_cell(rec, "Lung")
        assert c.value == pytest.approx(2.0)

    def test_zero_denominator_is_not_a_high_ratio(self) -> None:
        rec = GTExRecord("X.1", {"Lung": 5.0, "Liver": 0.0, "Brain": 0.0})
        c = specificity_cell(rec, "Lung")
        assert c.state == "no_data" and "undefined, not high" in c.detail

    def test_region_absent_is_no_data(self) -> None:
        assert specificity_cell(GTExRecord("X.1", {"Lung": 1.0}), "Spleen").state == "no_data"


class TestLiteratureQueryValidation:
    """The QA B3 correction: the echo does not catch this, so it never ships."""

    def test_unbalanced_quote_rejected_before_sending(self) -> None:
        with pytest.raises(QueryRejected, match="unbalanced double quote"):
            validate_query('"BRAF')

    def test_unbalanced_paren_rejected(self) -> None:
        with pytest.raises(QueryRejected, match="unbalanced paren"):
            validate_query("(BRAF AND knockout")

    def test_balanced_passes(self) -> None:
        assert validate_query('"BRAF" AND "hippocampus"')

    def test_gene_query_is_id_resolved_form(self) -> None:
        assert pubtator_gene_query("braf") == "@GENE_BRAF"
        assert pubtator_gene_query("BRAF", "lung") == "@GENE_BRAF lung"

    def test_failed_is_distinct_from_no_data(self) -> None:
        assert literature_cell(LiteratureRecord("X", "q", error="boom")).state == "failed"
        assert literature_cell(LiteratureRecord("X", "q")).state == "no_data"


class TestChEMBLStates:
    """COUNCIL D5: 'no record' and 'record with zero mechanisms' differ."""

    def test_no_record_is_not_undrugged(self) -> None:
        s = mechanism_summary(ChEMBLRecord("Q9NWM3", state="no_target_record"))
        assert "no data, not undrugged" in s["note"]

    def test_undrugged_is_stated_as_such(self) -> None:
        s = mechanism_summary(ChEMBLRecord("P1", state="undrugged", target_chembl_id="CHEMBL1"))
        assert "genuinely undrugged" in s["note"]

    def test_non_coding_has_no_accession(self) -> None:
        s = mechanism_summary(ChEMBLRecord(None, state="no_uniprot"))
        assert "non-coding" in s["note"]

    def test_always_marked_untrusted(self) -> None:
        s = mechanism_summary(ChEMBLRecord("P15056", state="mechanisms"))
        assert s["untrusted"] is True
        assert "unprobed" in s["untrusted_reason"]

    def test_direct_and_efficacy_filters(self) -> None:
        rec = ChEMBLRecord("P15056", state="mechanisms", mechanisms=[
            {"direct_interaction": 1, "disease_efficacy": 1},
            {"direct_interaction": 1, "disease_efficacy": 0},
            {"direct_interaction": 0, "disease_efficacy": 1},
        ])
        assert len(rec.direct_mechanisms) == 2
        assert len(rec.efficacy_mechanisms) == 1


# --- COUNCIL V4 / V8: sparse prioritisation keys ----------------------------

class TestSparsePrioritisationKeys:
    """COUNCIL V4 and V8.

    `hasSafetyEvent` and `isCancerDriverGene` are emitted by Open Targets only
    when they are negative; `hasHighQualityChemicalProbes` only when it is
    positive. v1's `_mean_of_keys` averaged whichever keys were present, which
    is arithmetically identical to imputing the absent ones at the mean of the
    present ones -- so a clean gene's "Safety" ring was constraint and
    essentiality alone, carrying no safety information, and rendered
    indistinguishably from a real 4-key mean.
    """

    @staticmethod
    def _rec(**prio):
        from targetring.adapters.opentargets import OTRecord
        return OTRecord("ENSG00000000001", found=True, prioritisation=dict(prio))

    def test_the_denominator_is_always_four(self) -> None:
        """V4's core: the denominator must stop encoding the answer."""
        from targetring.adapters.opentargets import safety_cell
        braf = self._rec(hasSafetyEvent=-1.0, isCancerDriverGene=-1.0,
                         geneEssentiality=0.0, geneticConstraint=-0.93)
        pcsk9 = self._rec(geneEssentiality=0.0, geneticConstraint=0.45)
        for cell in (safety_cell(braf), safety_cell(pcsk9)):
            assert cell.state == "value"
            assert "mean of 4/4 keys" in cell.detail
            assert len(cell.raw["keys"]) == 4

    def test_a_clean_gene_no_longer_gets_a_two_key_mean(self) -> None:
        from targetring.adapters.opentargets import safety_cell
        pcsk9 = self._rec(geneEssentiality=0.0, geneticConstraint=0.45)
        cell = safety_cell(pcsk9)
        # v1: mean(0.0, 0.45) = +0.225, the constraint number wearing a safety
        # ring's label. v2: mean(0, 0, 0, 0.45).
        assert cell.value == pytest.approx(0.1125)
        assert set(cell.raw["inferred_from_absence"]) == {
            "hasSafetyEvent", "isCancerDriverGene"}

    def test_the_cell_says_which_keys_were_read_and_which_inferred(self) -> None:
        from targetring.adapters.opentargets import safety_cell
        cell = safety_cell(self._rec(geneEssentiality=0.0, geneticConstraint=0.45))
        assert "hasSafetyEvent=+0.00*" in cell.detail
        assert "Absence of a record is not evidence" in cell.detail

    def test_v8_the_probe_key_is_no_longer_arithmetically_inert(self) -> None:
        """v1: BRAF mean(1,1,1,1) = 1.0 and PCSK9 mean(1,1,1) = 1.0. The one key
        that separates a chemically-probed target from an unprobed one changed
        nothing."""
        from targetring.adapters.opentargets import druggability_cell
        braf = self._rec(hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0,
                         hasHighQualityChemicalProbes=1.0)
        pcsk9 = self._rec(hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0)
        assert druggability_cell(braf).value == pytest.approx(1.0)
        assert druggability_cell(pcsk9).value == pytest.approx(0.75)
        assert druggability_cell(braf).value > druggability_cell(pcsk9).value

    def test_v8_the_probe_key_no_longer_reorders_genes_for_free(self) -> None:
        """v1: DDR1 mean(1,0,1,1)=0.75 outranked KCNH2 mean(1,0,1)=0.667 purely
        because DDR1's probe key existed. Both now score their own four keys."""
        from targetring.adapters.opentargets import druggability_cell
        ddr1 = self._rec(hasLigand=1.0, hasPocket=0.0, hasSmallMoleculeBinder=1.0,
                         hasHighQualityChemicalProbes=1.0)
        kcnh2 = self._rec(hasLigand=1.0, hasPocket=0.0, hasSmallMoleculeBinder=1.0)
        assert druggability_cell(ddr1).value == pytest.approx(0.75)
        assert druggability_cell(kcnh2).value == pytest.approx(0.50)

    def test_no_prioritisation_record_is_no_data_not_a_default_mean(self) -> None:
        """The sparse defaults must not manufacture a value for a target Open
        Targets does not score at all."""
        from targetring.adapters.opentargets import druggability_cell, safety_cell
        empty = self._rec()
        for cell in (safety_cell(empty), druggability_cell(empty)):
            assert cell.state == "no_data"
            assert "no prioritisation record" in cell.detail

    def test_a_missing_dense_key_refuses_a_shorter_mean(self) -> None:
        """If a key that is dense in the census goes absent, the denominator
        would differ from every other gene's -- which is the V4 defect. Refuse."""
        from targetring.adapters.opentargets import safety_cell
        cell = safety_cell(self._rec(geneticConstraint=0.4, hasLigand=1.0))
        assert cell.state == "no_data"
        assert "geneEssentiality" in cell.detail
        assert "denominator would encode the answer" in cell.detail


@pytest.mark.live
class TestSparseKeyCensusStillHolds:
    """The mapping above is a claim about the live API. If Open Targets starts
    emitting `hasSafetyEvent: 0` or `hasHighQualityChemicalProbes: 0`, the
    absent-value mapping is no longer a measured negative and this must fail
    rather than keep producing numbers."""

    @pytest.mark.asyncio
    async def test_sparse_keys_are_still_one_sided(self) -> None:
        from targetring.adapters.opentargets import SPARSE_KEY_ABSENT_VALUE
        from targetring.config import OPENTARGETS_URL
        from targetring.http import Http, graphql
        q = ("query P($ids:[String!]!){targets(ensemblIds:$ids)"
             "{id prioritisation{items{key value}}}}")
        ids = ["ENSG00000157764", "ENSG00000141510", "ENSG00000146648",
               "ENSG00000169174", "ENSG00000055118", "ENSG00000198133",
               "ENSG00000075624", "ENSG00000118271", "ENSG00000204580",
               "ENSG00000113161", "ENSG00000142627", "ENSG00000012048"]
        async with Http() as http:
            d = await graphql(http, OPENTARGETS_URL, q, {"ids": ids})
        seen: dict[str, set[float]] = {k: set() for k in SPARSE_KEY_ABSENT_VALUE}
        n_present = {k: 0 for k in SPARSE_KEY_ABSENT_VALUE}
        for t in d["targets"]:
            prio = {i["key"]: float(i["value"])
                    for i in (t.get("prioritisation") or {}).get("items", [])}
            for k in seen:
                if k in prio:
                    seen[k].add(round(prio[k], 3))
                    n_present[k] += 1
        for k, absent_value in SPARSE_KEY_ABSENT_VALUE.items():
            assert n_present[k] < len(d["targets"]), (
                f"{k} is now dense ({n_present[k]}/{len(d['targets'])}); it is no longer a "
                f"sparse-encoded key and SPARSE_KEY_ABSENT_VALUE must be revisited")
            assert absent_value not in seen[k], (
                f"Open Targets now emits {k}={absent_value} explicitly (observed "
                f"{sorted(seen[k])}). Absence is no longer that value, so mapping it to "
                f"{absent_value} would be imputation.")


# --- COUNCIL V1: polypharmacology must not set the ring to Approved ---------

class TestV1IndicationAttribution:
    """COUNCIL V1, reproduced from the council's own liver run.

    `clinical_phase` took max(maxClinicalStage) over every row with no test that
    the drug's indication had anything to do with the target, so a target with
    no approved drug rendered identically to one with three.
    """

    def test_epha2_is_no_longer_approved_by_three_other_targets_drugs(self) -> None:
        """EPHA2, run 20260924T064712Z-1bcd4f: clinical_phase = 5.0,
        "Approved (3 candidates)", brightest fill — on vandetanib (medullary
        thyroid, RET/VEGFR), dasatinib (CML, BCR-ABL1) and regorafenib
        (colorectal). There is no approved EPHA2 drug."""
        rec = ot(drug_count=3, drug_rows=[
            _row("APPROVAL", "MONDO_0015277", drug="VANDETANIB"),
            _row("APPROVAL", "MONDO_0011996", drug="DASATINIB"),
            _row("APPROVAL", "MONDO_0005575", drug="REGORAFENIB"),
        ])
        # Measured live 2026-09-24: none of the three carries a mechanistic
        # datatype for EPHA2. Colorectal is literature + rna_expression +
        # clinical; CML and medullary thyroid are clinical only.
        rec.associations = {
            "MONDO_0005575": {"literature": 0.781, "rna_expression": 0.033,
                              "clinical": 0.937},
            "MONDO_0011996": {"literature": 0.135, "clinical": 0.948},
            "MONDO_0015277": {"clinical": 0.892},
        }
        c = clinical_phase_cell(rec)
        assert c.state == "not_applicable", "an unattributed approval is not a phase"
        assert c.value is None, "and must not be a lower number on the same scale"
        assert "NONE is indicated for a disease" in c.detail
        assert "Approved" in c.detail, "the excluded finding still has to be visible"
        assert c.raw["unattributed_max_stage"] == 5

    def test_braf_keeps_its_approval(self) -> None:
        """The rule must not destroy the true positive: BRAF x melanoma carries
        genetic_association 0.699."""
        rec = ot(drug_count=2, drug_rows=[
            _row("APPROVAL", "MONDO_0005105", drug="VEMURAFENIB"),
            _row("PHASE_2", "MONDO_0005575", drug="SORAFENIB"),
        ])
        rec.associations = {
            "MONDO_0005105": {"somatic_mutation": 0.796, "genetic_association": 0.699,
                              "affected_pathway": 0.434, "clinical": 0.978,
                              "literature": 0.998},
            "MONDO_0005575": {"genetic_association": 0.771, "clinical": 0.941},
        }
        c = clinical_phase_cell(rec)
        assert c.state == "value" and c.value == 5.0
        assert "VEMURAFENIB" in c.detail and "genetic_association 0.70" in c.detail

    def test_egfr_the_canonical_oncology_on_target_approval_survives(self) -> None:
        """The false-negative worry for a genetics-only rule is oncology, where
        the driver is somatic. Measured: EGFR x NSCLC carries
        genetic_association 0.75 and genetic_literature 0.61, so afatinib keeps
        its approval."""
        rec = ot(drug_count=82, drug_rows=[_row("APPROVAL", "MONDO_0005233",
                                                drug="AFATINIB")])
        rec.associations = {"MONDO_0005233": {
            "genetic_association": 0.75, "genetic_literature": 0.61,
            "somatic_mutation": 0.83, "affected_pathway": 0.82, "clinical": 1.0}}
        assert clinical_phase_cell(rec).value == 5.0

    def test_sorafenib_for_renal_cell_carcinoma_does_not_attribute(self) -> None:
        """PLAN §2 names this case: "BRAF's top row is sorafenib, approved for
        RCC/HCC, not BRAF-driven disease". Measured: genetics 0.000 for both,
        somatic_mutation 0.304/0.456 -- which is BRAF's pan-cancer floor, not
        disease-specific evidence."""
        rec = ot(drug_count=2, drug_rows=[
            _row("APPROVAL", "MONDO_0005005", drug="SORAFENIB"),   # RCC
            _row("APPROVAL", "MONDO_0007256", drug="SORAFENIB"),   # HCC
        ])
        rec.associations = {
            "MONDO_0005005": {"somatic_mutation": 0.304, "literature": 0.232,
                              "clinical": 0.950},
            "MONDO_0007256": {"somatic_mutation": 0.456, "literature": 0.670,
                              "clinical": 0.973},
        }
        assert clinical_phase_cell(rec).state == "not_applicable"

    def test_the_pan_cancer_somatic_floor_does_not_attribute(self) -> None:
        """BRAF returns somatic_mutation = 0.456 for thirty-odd unrelated tumour
        types. A datatype that fires everywhere discriminates nowhere."""
        rec = ot(drug_count=1, drug_rows=[_row("APPROVAL", "MONDO_RHABDO")])
        rec.associations = {"MONDO_RHABDO": {"somatic_mutation": 0.456}}
        assert clinical_phase_cell(rec).state == "not_applicable"

    def test_pathway_membership_does_not_attribute(self) -> None:
        """EPHA2 x squamous cell lung carcinoma = affected_pathway 0.760, purely
        through Eph receptor signalling."""
        rec = ot(drug_count=1, drug_rows=[_row("APPROVAL", "MONDO_SCC")])
        rec.associations = {"MONDO_SCC": {"affected_pathway": 0.760,
                                          "literature": 0.104, "clinical": 0.061}}
        assert clinical_phase_cell(rec).state == "not_applicable"

    def test_the_strongest_evidence_row_is_the_one_named(self) -> None:
        """Ties at stage 5 are common -- BRAF has 18 approved candidates -- and
        naming the first would put a weak exemplar on a well-attributed ring."""
        rec = ot(drug_count=2, drug_rows=[
            _row("APPROVAL", "MONDO_WEAK", drug="WEAK"),
            _row("APPROVAL", "MONDO_STRONG", drug="STRONG"),
        ])
        rec.associations = {"MONDO_WEAK": {"genetic_association": 0.15},
                            "MONDO_STRONG": {"genetic_association": 0.90}}
        assert "STRONG" in clinical_phase_cell(rec).detail

    def test_circular_evidence_alone_never_attributes(self) -> None:
        """`clinical` IS the drug, and `literature` co-mention is inflated by the
        trials themselves. Either alone must not make an indication count."""
        for dts in ({"clinical": 0.99}, {"literature": 0.99},
                    {"clinical": 0.99, "literature": 0.99, "rna_expression": 0.5}):
            rec = ot(drug_count=1, drug_rows=[_row("APPROVAL", "MONDO_X")])
            rec.associations = {"MONDO_X": dts}
            assert clinical_phase_cell(rec).state == "not_applicable", dts

    def test_a_trace_level_score_is_not_attribution(self) -> None:
        """EPHA2 x melanoma carries genetic_association = 0.0 exactly."""
        rec = ot(drug_count=1, drug_rows=[_row("APPROVAL", "MONDO_0005105")])
        rec.associations = {"MONDO_0005105": {"genetic_association": 0.0,
                                              "literature": 0.875}}
        assert clinical_phase_cell(rec).state == "not_applicable"
        rec.associations = {"MONDO_0005105": {"genetic_association": 0.2}}
        assert clinical_phase_cell(rec).value == 5.0

    def test_the_excluded_higher_stage_is_reported_not_hidden(self) -> None:
        rec = ot(drug_count=2, drug_rows=[
            _row("PHASE_1", "MONDO_OWN", drug="ONTARGET"),
            _row("APPROVAL", "MONDO_OTHER", drug="PROMISCUOUS"),
        ])
        rec.associations = {"MONDO_OWN": {"genetic_association": 0.9},
                            "MONDO_OTHER": {"clinical": 0.9}}
        c = clinical_phase_cell(rec)
        assert c.value == 2.0, "Phase 1, on the attributable drug"
        assert "Approved was reached by a candidate whose indication is NOT attributable" \
            in c.detail

    def test_a_failed_attribution_query_is_failed_not_unattributed(self) -> None:
        """'We asked and could not find out' is not 'nothing is attributable'.
        Guessing either way would be a confident wrong answer."""
        rec = ot(drug_count=3, drug_rows=[_row("APPROVAL", "MONDO_X")])
        rec.associations_error = "HTTP 503 from api.platform.opentargets.org"
        c = clinical_phase_cell(rec)
        assert c.state == "failed" and c.value is None
        assert "503" in c.detail

    def test_zero_drugs_is_still_a_meaningful_zero(self) -> None:
        """COUNCIL D5 must survive the change."""
        rec = ot(drug_count=0)
        rec.associations = {}
        c = clinical_phase_cell(rec)
        assert c.state == "value" and c.value == 0.0


class TestV1SevereLiabilityFlag:
    """COUNCIL V1 (ii). KCNH2 rendered druggable, "Approved (17 candidates)" and
    actively trialled while its own safetyLiabilities — fetched in the same call
    — were Torsades de Point, Increased Mortality and Reduced survival."""

    def test_kcnh2_is_flagged(self) -> None:
        rec = ot(safety_liabilities=[
            {"event": "Torsades de Point", "datasource": "AOP"},
            {"event": "Increased Mortality", "datasource": "AOP"},
            {"event": "Reduced, survival", "datasource": "AOP"},
            {"event": "prolongation of QT interval of ECG", "datasource": "AOP"},
            {"event": "receptor binding", "datasource": "AOP"},
        ])
        flags = severe_liabilities(rec)
        assert {f["event"] for f in flags} >= {"Torsades de Point", "Increased Mortality"}
        assert "receptor binding" not in {f["event"] for f in flags}
        # The matched term travels with the flag, so the claim is auditable.
        assert all(f["matched"] for f in flags)

    def test_a_target_with_no_cardiac_or_mortality_record_is_not_flagged(self) -> None:
        rec = ot(safety_liabilities=[
            {"event": "Skin rash"}, {"event": "alopecia"}, {"event": "diarrhoea"},
        ])
        assert severe_liabilities(rec) == []

    def test_no_safety_record_at_all_is_not_flagged(self) -> None:
        assert severe_liabilities(ot()) == []

    def test_the_flag_reaches_the_figure(self, tmp_path) -> None:
        from targetring.figure import render
        from targetring.rank import rank_genes
        from targetring.states import value as v
        spec = {"logfc": {"KCNH2": v(1.0), "PCSK9": v(2.0)},
                "druggability": {"KCNH2": v(0.5), "PCSK9": v(0.9)}}
        render(genes=["KCNH2", "PCSK9"], cells_by_ring=spec,
               rankings=rank_genes(spec), region="Liver",
               out_svg=tmp_path / "f.svg", input_count=2, rendered_count=2,
               ledger_counts={}, gene_flags={"KCNH2": ["Torsades de Point"]})
        svg = (tmp_path / "f.svg").read_text()
        assert "⚠ KCNH2" in svg or "KCNH2" in svg
        assert "cardiac-conduction, cardiotoxicity or mortality" in svg
        assert "never a safety one" in svg
