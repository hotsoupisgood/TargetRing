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

    def test_max_stage_wins(self) -> None:
        rec = ot(drug_count=3, drug_rows=[
            {"maxClinicalStage": "PHASE_1"},
            {"maxClinicalStage": "APPROVAL"},
            {"maxClinicalStage": "PHASE_2"},
        ])
        assert clinical_phase_cell(rec).value == 5.0

    def test_unmapped_stage_does_not_become_zero(self) -> None:
        rec = ot(drug_count=2, drug_rows=[{"maxClinicalStage": None},
                                          {"maxClinicalStage": "SOMETHING_NEW"}])
        c = clinical_phase_cell(rec)
        assert c.value == 1.0 and "none with a mapped clinical stage" in c.detail


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
    def test_no_abnormal_phenotype_is_excluded_from_the_count(self) -> None:
        """QA S4: counting rows is not counting abnormal phenotypes."""
        rec = ot(mouse_phenotypes=[
            {"modelPhenotypeLabel": "no abnormal phenotype detected"},
            {"modelPhenotypeLabel": "abnormal heart morphology"},
        ])
        c = rodent_cell(rec)
        assert c.value == 1.0
        assert "1 'no abnormal phenotype' rows excluded" in c.detail

    def test_phenotyped_but_normal_is_a_measured_zero(self) -> None:
        rec = ot(mouse_phenotypes=[{"modelPhenotypeLabel": "no abnormal phenotype detected"}])
        c = rodent_cell(rec)
        assert c.state == "value" and c.value == 0.0

    def test_never_phenotyped_is_no_data(self) -> None:
        assert rodent_cell(ot(mouse_phenotypes=[])).state == "no_data"


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
