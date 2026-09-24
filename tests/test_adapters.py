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
    per_indication_stages,
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


def _row(stage, *disease_ids, drug="DRUG", per_indication=None):
    """One drugAndClinicalCandidates row with its indications.

    `maxClinicalStage` is the drug's *global* maximum across every indication on
    the row; the stage reached for one particular indication lives in
    `clinicalReports` (QA-v2-retest §2). By default every listed indication gets
    a report at the row's own stage -- the case where the two agree. Pass
    `per_indication={disease_id: STAGE_OR_None}` for the case where they do not,
    which is the FRK x REGORAFENIB defect.
    """
    reports = []
    for d in disease_ids:
        own = (per_indication or {}).get(d, stage)
        if own is None:
            continue
        reports.append({
            "clinicalStage": own, "trialPhase": None, "source": "ClinicalTrials.gov",
            "diseases": [{"disease": {"id": d, "name": d.lower()}}],
        })
    return {
        "maxClinicalStage": stage,
        "drug": {"id": "CHEMBL1", "name": drug},
        "diseases": [{"disease": {"id": d, "name": d.lower()}} for d in disease_ids],
        "clinicalReports": reports,
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
        nothing.

        v2.2 item 1: Open Targets now writes the probe key explicitly as 0.0, so
        this compares two *stated* values rather than a stated one and a silence.
        """
        from targetring.adapters.opentargets import druggability_cell
        braf = self._rec(hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0,
                         hasHighQualityChemicalProbes=1.0)
        pcsk9 = self._rec(hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0,
                          hasHighQualityChemicalProbes=0.0)
        assert druggability_cell(braf).value == pytest.approx(1.0)
        assert druggability_cell(pcsk9).value == pytest.approx(0.75)
        assert druggability_cell(braf).value > druggability_cell(pcsk9).value

    def test_v8_the_probe_key_no_longer_reorders_genes_for_free(self) -> None:
        """v1: DDR1 mean(1,0,1,1)=0.75 outranked KCNH2 mean(1,0,1)=0.667 purely
        because DDR1's probe key existed. Both now score their own four keys."""
        from targetring.adapters.opentargets import druggability_cell
        ddr1 = self._rec(hasLigand=1.0, hasPocket=0.0, hasSmallMoleculeBinder=1.0,
                         hasHighQualityChemicalProbes=1.0)
        kcnh2 = self._rec(hasLigand=1.0, hasPocket=0.0, hasSmallMoleculeBinder=1.0,
                          hasHighQualityChemicalProbes=0.0)
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


class TestExplicitZeroProbeKey:
    """v2.2 item 1 — `hasHighQualityChemicalProbes` stopped being one-sided.

    Open Targets used to omit the key when a target had no high-quality probe.
    It now also writes `0.0` explicitly, so absence and a measured zero coexist
    and the V4/V8 mapping of absence to 0.0 became imputation. Puck's decision,
    2026-09-24: an explicit 0.0 is a real value meaning "looked, none exists";
    a genuinely absent key is `no_data`.
    """

    @staticmethod
    def _rec(**prio):
        from targetring.adapters.opentargets import OTRecord
        return OTRecord("ENSG00000000001", found=True, prioritisation=dict(prio))

    def test_the_key_is_not_inferred_from_absence_any_more(self) -> None:
        from targetring.adapters.opentargets import (
            EXPLICIT_ZERO_KEYS,
            SPARSE_KEY_ABSENT_VALUE,
        )
        assert "hasHighQualityChemicalProbes" not in SPARSE_KEY_ABSENT_VALUE, (
            "mapping absence to 0.0 while Open Targets also writes 0.0 explicitly is "
            "imputation; that is what the census test caught")
        assert "hasHighQualityChemicalProbes" in EXPLICIT_ZERO_KEYS

    def test_an_explicit_zero_is_a_real_value(self) -> None:
        """'Looked, none exists' is information, and it does arithmetic work."""
        from targetring.adapters.opentargets import druggability_cell
        cell = druggability_cell(self._rec(
            hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0,
            hasHighQualityChemicalProbes=0.0))
        assert cell.state == "value"
        assert cell.value == pytest.approx(0.75)
        assert "mean of 4/4 keys" in cell.detail
        assert cell.raw["inferred_from_absence"] == [], "read, not inferred"
        assert "hasHighQualityChemicalProbes=+0.00" in cell.detail
        assert "hasHighQualityChemicalProbes=+0.00*" not in cell.detail, (
            "the * marks a key inferred from absence; this one was stated")

    def test_an_absent_key_is_no_data_and_says_why(self) -> None:
        """The other half of the decision. A shorter mean is the V4 defect and a
        substituted 0.0 is the imputation, so the cell refuses both."""
        from targetring.adapters.opentargets import druggability_cell
        cell = druggability_cell(self._rec(
            hasLigand=1.0, hasPocket=1.0, hasSmallMoleculeBinder=1.0))
        assert cell.state == "no_data"
        assert cell.value is None
        assert "hasHighQualityChemicalProbes" in cell.detail
        assert "imputation" in cell.detail
        assert "denominator would encode the answer" in cell.detail

    def test_the_two_liability_keys_are_still_inferred_from_absence(self) -> None:
        """The decision is about one key. `hasSafetyEvent` and
        `isCancerDriverGene` are still emitted only at the -1.0 end (live census
        2026-09-24), so absence there is still a measured negative."""
        from targetring.adapters.opentargets import safety_cell
        cell = safety_cell(self._rec(geneEssentiality=0.0, geneticConstraint=0.45))
        assert cell.state == "value"
        assert set(cell.raw["inferred_from_absence"]) == {
            "hasSafetyEvent", "isCancerDriverGene"}


@pytest.mark.live
class TestSparseKeyCensusStillHolds:
    """The mapping is a claim about the live API, and the claim broke once.

    v2 checked only the keys in SPARSE_KEY_ABSENT_VALUE, so it caught
    `hasHighQualityChemicalProbes` acquiring an explicit 0.0 — but only because
    that key happened to be mapped. The census now walks **every** prioritisation
    key Open Targets emits, so a key acquiring the same behaviour is caught
    whether or not this build currently reads it.
    """

    KEY_IDS = ["ENSG00000157764", "ENSG00000141510", "ENSG00000146648",
               "ENSG00000169174", "ENSG00000055118", "ENSG00000198133",
               "ENSG00000075624", "ENSG00000118271", "ENSG00000204580",
               "ENSG00000113161", "ENSG00000142627", "ENSG00000012048"]

    @staticmethod
    async def _census(ids):
        from targetring.config import OPENTARGETS_URL
        from targetring.http import Http, graphql
        q = ("query P($ids:[String!]!){targets(ensemblIds:$ids)"
             "{id prioritisation{items{key value}}}}")
        async with Http() as http:
            d = await graphql(http, OPENTARGETS_URL, q, {"ids": ids})
        seen: dict[str, set[float]] = {}
        n_present: dict[str, int] = {}
        for t in d["targets"]:
            prio = {i["key"]: float(i["value"])
                    for i in (t.get("prioritisation") or {}).get("items", [])}
            for k, v in prio.items():
                seen.setdefault(k, set()).add(round(v, 3))
                n_present[k] = n_present.get(k, 0) + 1
        return len(d["targets"]), seen, n_present

    @pytest.mark.asyncio
    async def test_mapped_sparse_keys_are_still_one_sided(self) -> None:
        """If Open Targets starts emitting `hasSafetyEvent: 0`, absence there is
        no longer a measured negative and the mapping becomes imputation."""
        from targetring.adapters.opentargets import SPARSE_KEY_ABSENT_VALUE
        n, seen, n_present = await self._census(self.KEY_IDS)
        for k, absent_value in SPARSE_KEY_ABSENT_VALUE.items():
            assert n_present.get(k, 0) < n, (
                f"{k} is now dense ({n_present.get(k, 0)}/{n}); it is no longer a "
                f"sparse-encoded key and SPARSE_KEY_ABSENT_VALUE must be revisited")
            assert absent_value not in seen.get(k, set()), (
                f"Open Targets now emits {k}={absent_value} explicitly (observed "
                f"{sorted(seen.get(k, set()))}). Absence is no longer that value, so "
                f"mapping it to {absent_value} would be imputation.")

    @pytest.mark.asyncio
    async def test_the_probe_key_really_does_carry_an_explicit_zero(self) -> None:
        """The premise of v2.2 item 1, asserted against the source rather than
        remembered. If Open Targets goes back to omitting the key the decision
        needs revisiting, and this says so instead of going quiet."""
        n, seen, n_present = await self._census(self.KEY_IDS)
        k = "hasHighQualityChemicalProbes"
        assert 0.0 in seen.get(k, set()), (
            f"{k} no longer carries an explicit 0.0 (observed {sorted(seen.get(k, set()))}); "
            f"the 'explicit 0.0 means looked-and-none' reading rests on it")
        assert n_present.get(k, 0) < n, (
            f"{k} is now dense ({n_present.get(k, 0)}/{n}); absence is no longer a state "
            f"the ring has to handle and the no_data branch is dead code")

    @pytest.mark.asyncio
    async def test_no_other_key_has_quietly_acquired_an_explicit_zero(self) -> None:
        """The generalisation. A sparse key that also emits 0.0 explicitly can no
        longer have its absence read as a zero — whoever reads it. Any key that
        is not already accounted for is a new finding and fails here."""
        from targetring.adapters.opentargets import (
            EXPLICIT_ZERO_KEYS,
            EXPLICIT_ZERO_KEYS_NOT_READ,
        )
        n, seen, n_present = await self._census(self.KEY_IDS)
        known = set(EXPLICIT_ZERO_KEYS) | set(EXPLICIT_ZERO_KEYS_NOT_READ)
        surprises = {
            k: sorted(vals) for k, vals in seen.items()
            if 0.0 in vals and n_present[k] < n and k not in known
        }
        assert not surprises, (
            f"these prioritisation keys are sparse AND emit an explicit 0.0, which is the "
            f"hasHighQualityChemicalProbes situation on a new key: {surprises}. Absence in "
            f"them can no longer be read as a zero; decide per key what absence means.")


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


class TestV1PerIndicationPhase:
    """QA-v2-retest §2. The V1 fix held as a mechanism and failed as an outcome.

    `maxClinicalStage` is the *drug's* global maximum across every indication on
    its row -- ~25 of them for a promiscuous drug -- so once the attribution gate
    opened on a genuine target-disease link, the stage reported belonged to a
    different disease. FRK rendered 5.0, "Approved", brightest fill, on
    REGORAFENIB x age-related macular degeneration. FRK has no approved drug and
    regorafenib is not approved for AMD.

    The phase must be the phase FOR THE ATTRIBUTED INDICATION, or no number.
    """

    # Measured live from Open Targets, 2026-09-24, ENSG00000111816 (`FRK`):
    # regorafenib's row carries maxClinicalStage = PHASE_4 and 134 indications.
    # Per indication, off `clinicalReports`:
    #     colorectal cancer  MONDO_0005575  APPROVAL  (DailyMed, PMDA)
    #     GIST               MONDO_0011719  PHASE_3
    #     AMD                MONDO_0005150  PHASE_2   <- the one FRK is credited with
    FRK_AMD = "MONDO_0005150"
    FRK_CRC = "MONDO_0005575"

    def _frk(self):
        rec = ot(ensembl_id="ENSG00000111816", drug_count=6, drug_rows=[
            _row("PHASE_4", self.FRK_CRC, self.FRK_AMD, drug="REGORAFENIB",
                 per_indication={self.FRK_CRC: "APPROVAL", self.FRK_AMD: "PHASE_2"}),
        ])
        # Live: FRK x AMD carries genetic_association 0.41, so the attribution
        # gate correctly opens. FRK x colorectal carries no genetics at all.
        rec.associations = {
            self.FRK_AMD: {"genetic_association": 0.41, "literature": 0.22},
            self.FRK_CRC: {"literature": 0.31, "clinical": 0.94},
        }
        return rec

    def test_frk_does_not_render_approved_on_regorafenib_for_amd(self) -> None:
        c = clinical_phase_cell(self._frk())
        assert c.value != 5.0, "FRK has no approved drug; 'Approved' is the defect"
        assert c.state == "value" and c.value == 3.0, "Phase 2 — what AMD reached"
        assert "age-related" in c.detail.lower() or self.FRK_AMD.lower() in c.detail.lower()

    def test_the_borrowed_approval_is_named_as_excluded_not_silently_dropped(self) -> None:
        """The drug really did reach Approved — for another disease. Saying so is
        the difference between a correction and a disappearance."""
        c = clinical_phase_cell(self._frk())
        assert "DIFFERENT indication" in c.detail
        assert c.raw["global_stage_excluded"] == 5

    def test_per_indication_stages_reads_the_reports_not_the_row(self) -> None:
        row = self._frk().drug_rows[0]
        stages, unmapped = per_indication_stages(row)
        assert stages[self.FRK_CRC] == 5 and stages[self.FRK_AMD] == 3
        assert row["maxClinicalStage"] == "PHASE_4", "the global stage is still 5 on the row"
        assert not unmapped

    def test_an_unmapped_report_stage_is_never_guessed_at(self) -> None:
        """Open Targets emits EARLY_PHASE_1, UNKNOWN and WITHDRAWAL, which this
        build does not map. They must not become a low number or a high one."""
        row = _row("PHASE_2", "MONDO_X", per_indication={"MONDO_X": "UNKNOWN"})
        stages, unmapped = per_indication_stages(row)
        assert stages == {} and unmapped == {"UNKNOWN"}

    def test_attributed_but_unstageable_is_no_data_not_the_global_stage(self) -> None:
        """The whole point: with no per-indication stage behind a genuine
        attribution, the answer is 'we could not establish one' — never the
        drug's global number."""
        rec = ot(drug_count=4, drug_rows=[
            _row("APPROVAL", "MONDO_OWN", drug="PROMISCUOUS",
                 per_indication={"MONDO_OWN": None}),
        ])
        rec.associations = {"MONDO_OWN": {"genetic_association": 0.8}}
        c = clinical_phase_cell(rec)
        assert c.state == "no_data" and c.value is None
        assert "PROMISCUOUS" in c.detail and "global maxClinicalStage" in c.detail

    def test_the_councils_epha2_case_still_holds_at_the_stage_level_too(self) -> None:
        """COUNCIL V1's original: EPHA2, "Approved (3 candidates)", on
        vandetanib, dasatinib and regorafenib. The gate closes on the score —
        but the fixture now also carries the per-indication stages, so the test
        pins BOTH halves: nothing is attributable, AND the APPROVAL on the row
        belongs to colorectal cancer, not to anything EPHA2 is credited with."""
        rec = ot(drug_count=3, drug_rows=[
            _row("APPROVAL", "MONDO_0015277", drug="VANDETANIB"),
            _row("APPROVAL", "MONDO_0011996", drug="DASATINIB"),
            _row("PHASE_4", "MONDO_0005575", "MONDO_0005150", drug="REGORAFENIB",
                 per_indication={"MONDO_0005575": "APPROVAL", "MONDO_0005150": "PHASE_2"}),
        ])
        rec.associations = {
            "MONDO_0005575": {"literature": 0.781, "rna_expression": 0.033,
                              "clinical": 0.937},
            "MONDO_0005150": {"literature": 0.104},
            "MONDO_0011996": {"literature": 0.135, "clinical": 0.948},
            "MONDO_0015277": {"clinical": 0.892},
        }
        c = clinical_phase_cell(rec)
        assert c.state == "not_applicable" and c.value is None
        assert c.raw["unattributed_max_stage"] == 5

    def test_braf_names_vemurafenib_not_sorafenib(self) -> None:
        """QA-v2-retest §3. The max-evidence tiebreak named SORAFENIB TOSYLATE x
        lung cancer (genetics 0.83) over VEMURAFENIB x melanoma (0.699) — the
        exact drug the V1 fix exists to reject, and it is not approved for lung
        cancer. Measured live: sorafenib reaches PHASE_2 for lung cancer while
        vemurafenib reaches APPROVAL for melanoma, so ranking on the stage
        reached FOR THAT INDICATION removes the tie the tiebreak was losing."""
        rec = ot(drug_count=18, drug_rows=[
            _row("PHASE_4", "MONDO_0005105", drug="VEMURAFENIB",
                 per_indication={"MONDO_0005105": "APPROVAL"}),
            _row("APPROVAL", "MONDO_0008903", drug="SORAFENIB TOSYLATE",
                 per_indication={"MONDO_0008903": "PHASE_2"}),
        ])
        rec.associations = {
            "MONDO_0005105": {"genetic_association": 0.699, "somatic_mutation": 0.796},
            "MONDO_0008903": {"genetic_association": 0.83},
        }
        c = clinical_phase_cell(rec)
        assert c.value == 5.0
        assert "VEMURAFENIB" in c.detail
        assert "SORAFENIB" not in c.detail


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


class TestAFailureAlwaysCarriesAReason:
    """Found while EBI was returning HTTP 500 for every ChEMBL request during
    the v2 screenshot pass: several httpx exceptions carry no message, so
    `str(e)` was `''` and a `failed` record's reason was the empty string.

    The legend promises "call FAILED — not the same as absent". A failure with
    no reason keeps the distinction and throws away the only thing that makes it
    actionable — and it is what made the ChEMBL contract test report FAIL for a
    source that was simply down.
    """

    def test_describe_is_never_empty(self) -> None:
        import httpx

        from targetring.http import describe
        for e in (httpx.ReadError(""), httpx.ConnectError(""), RuntimeError(),
                  httpx.TimeoutException(""), ValueError("")):
            got = describe(e)
            assert got.strip(), f"{type(e).__name__} produced an empty reason"
            assert type(e).__name__ in got

    def test_a_message_is_kept_when_there_is_one(self) -> None:
        from targetring.http import describe
        got = describe(RuntimeError("HTTP 500 from ebi.ac.uk"))
        assert "HTTP 500 from ebi.ac.uk" in got and "RuntimeError" in got

    def test_no_adapter_records_a_bare_str_of_an_exception(self) -> None:
        """The whole class of defect, not just the one instance.

        Scoped to the adapters and the contract checks, where the exception
        comes off a network call and its type is arbitrary. `app.py` is excluded
        on purpose: it stringifies exceptions this codebase raises itself
        (InputRejected, HardFailure, the accounting AssertionError), all of which
        carry a written message, and the API response names the class in its own
        `error` key.
        """
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "targetring"
        files = list((root / "adapters").rglob("*.py")) + [root / "contract.py"]
        offenders = []
        for f in files:
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if "str(e)" in line and "describe" not in line:
                    offenders.append(f"{f.name}:{i}: {line.strip()}")
        assert not offenders, (
            "record failures with `describe(e)`, which always names the exception "
            "type: " + "; ".join(offenders))


class TestTheActivityRingSaysWhatItLookedFor:
    """Found while re-running v2.1, 2026-09-24: Open Targets stopped emitting
    `AACT` as a clinicalReports source and now labels the same trials
    `ClinicalTrials.gov` -- 0 AACT rows across 109 targets today against 4,195
    in the previous night's cache. `clinical_activity` reads AACT only, so the
    whole ring went to `no_data` for every gene.

    That is honest (hatched, off-colormap, never imputed) but it was mute: the
    detail named the source it wanted and not the ones it got, so a reader
    could not tell an upstream rename from a target with no trials. Changing
    what the ring counts is a source-semantics decision and not one to take
    unattended; saying what happened is not.
    """

    def test_the_detail_names_the_sources_that_were_there(self) -> None:
        rec = ot(drug_count=2, drug_rows=[{"clinicalReports": [
            {"source": "ClinicalTrials.gov", "trialOverallStatus": "RECRUITING"},
            {"source": "DailyMed", "trialOverallStatus": None},
        ]}])
        c = clinical_activity_cell(rec)
        assert c.state == "no_data" and c.value is None
        assert "ClinicalTrials.gov" in c.detail and "DailyMed" in c.detail
        assert "AACT" in c.detail, "and which source it was looking for"

    def test_a_target_with_no_reports_at_all_still_reads_cleanly(self) -> None:
        rec = ot(drug_count=1, drug_rows=[{"clinicalReports": []}])
        c = clinical_activity_cell(rec)
        assert c.state == "no_data"
        assert "none" in c.detail
