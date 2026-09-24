"""The activity ring, read off Open Targets' own `clinicalReports` rows.

**History, because it is the point of this file.** v2.2 item 2 replaced this
ring's source with a per-drug hop to ClinicalTrials.gov, on the evidence that
Open Targets had dropped its trial data. It had not: on 2026-09-24 Open Targets
relabelled `clinicalReports.source` from `AACT` to `ClinicalTrials.gov`, this
build filtered on the old literal, matched nothing, and concluded the data was
gone. The hop cost 217 requests on a 50-gene run, 48.2 s of a 70.5 s cold run
and one live 429, for data that was already in the gene query.

These tests were written for that adapter and are kept, re-pointed at the code
that does the work now. Every assertion that still has meaning is still here:
the ring names its source, the states are exhaustive, an unknown status is a
loud failure rather than a silent bucket, evidence resolves in one direction
only, the drawer counts distinct NCT ids, and sponsor plus live status reach the
drawer. Three things are asserted that could not be before -- that no *source
literal* selects rows, that the labels seen are recorded, and that a stop
categorised Negative or Safety_Sideeffects is distinguished from one categorised
Insufficient_Enrollment (v2.4 item 2).
"""

from __future__ import annotations

import pytest

from targetring.adapters.opentargets import (
    FOR_CAUSE_CATEGORIES,
    INERT_STATUSES,
    LIVE_STATUSES,
    SOURCE_LABEL,
    STOPPED_STATUSES,
    OTRecord,
    classify_status,
    clinical_activity_cell,
    drug_pipeline_rows,
    nct_id,
    source_counts,
    trial_reports,
    trials_panel,
)


def report(
    nct: str | None = "nct00000001",
    *,
    status: str = "RECRUITING",
    source: str = "ClinicalTrials.gov",
    sponsor: str | None = "Sponsor A",
    categories: list[str] | None = None,
    why: str | None = None,
) -> dict:
    return {
        "id": nct,
        "trialOverallStatus": status,
        "source": source,
        "trialSponsor": {"name": sponsor} if sponsor else None,
        "trialStopReasonCategories": categories or [],
        "trialWhyStopped": why,
        "trialPhase": "PHASE2",
        "clinicalStage": "PHASE_2",
        "trialStartDate": "2020-01-01",
        "diseases": [{"disease": {"id": "MONDO_1", "name": "a disease"}}],
        "url": None,
    }


def rec(*drugs: tuple[str, list[dict]], count: int | None = None) -> OTRecord:
    """One Open Targets record with the given drugs and their trial reports."""
    rows = [{"drug": {"id": f"CHEMBL{i}", "name": name},
             "maxClinicalStage": "PHASE_2",
             "diseases": [{"disease": {"id": "MONDO_1", "name": "a disease"}}],
             "clinicalReports": reports}
            for i, (name, reports) in enumerate(drugs)]
    return OTRecord("ENSG00000000001", found=True, approved_symbol="TESTG",
                    drug_count=count if count is not None else len(rows),
                    drug_rows=rows)


def live_n(n: int, *, first: int = 0, sponsor: str | None = "Sponsor A") -> list[dict]:
    return [report(f"nct{i:08d}", status="RECRUITING", sponsor=sponsor)
            for i in range(first, first + n)]


def stopped_n(n: int, *, first: int = 0, categories: list[str] | None = None,
              why: str | None = "slow accrual led to early closure.") -> list[dict]:
    return [report(f"nct9{i:07d}", status="TERMINATED", sponsor="Stopper Inc",
                   categories=categories, why=why)
            for i in range(first, first + n)]


class TestNoSourceLiteralSelectsRows:
    """v2.4, and the defect this whole change is about. A filter on one `source`
    string is a filter on a label a third party owns; when Open Targets renamed
    it, the ring went dark and a replacement API was built for data that had
    never left."""

    def test_a_relabelled_source_still_counts(self) -> None:
        for label in ("AACT", "ClinicalTrials.gov", "SomethingNobodyHasSeenYet"):
            c = clinical_activity_cell(
                rec(("A", [report(status="RECRUITING", source=label)])))
            assert c.value == 1.0, f"a row labelled {label} emptied the ring"

    def test_what_selects_a_row_is_that_it_carries_a_status(self) -> None:
        """COUNCIL D1 gap (ii) without naming a label: TTD / EMA / ATC rows have
        no `trialOverallStatus` and would read as inactive if counted."""
        r = rec(("A", [report("nct00000001", status="RECRUITING"),
                       {"id": "eme123", "source": "EMA Human Drugs",
                        "trialOverallStatus": None},
                       {"id": "l01xe", "source": "ATC", "trialOverallStatus": None}]))
        rows = trial_reports(r)
        assert len(rows) == 1 and rows[0]["nct_id"] == "NCT00000001"

    def test_the_source_labels_seen_are_recorded(self) -> None:
        """So the next relabelling is visible in the artefacts, not silent."""
        r = rec(("A", [report("nct00000001", source="ClinicalTrials.gov"),
                       report("nct00000002", source="ClinicalTrials.gov"),
                       {"id": "t1", "source": "TTD", "trialOverallStatus": None}]))
        assert source_counts(r) == {"ClinicalTrials.gov": 2, "TTD": 1}
        c = clinical_activity_cell(r)
        assert c.raw["sources_seen"] == {"ClinicalTrials.gov": 2, "TTD": 1}
        assert "ClinicalTrials.gov 2" in c.detail

    def test_no_rows_with_a_status_names_what_was_there(self) -> None:
        """The diagnosability that made the relabelling findable at all, kept."""
        r = rec(("A", [{"id": "t1", "source": "TTD", "trialOverallStatus": None},
                       {"id": "e1", "source": "EMA Human Drugs",
                        "trialOverallStatus": None}]))
        c = clinical_activity_cell(r)
        assert c.state == "no_data"
        assert "TTD 1" in c.detail and "EMA Human Drugs 1" in c.detail


class TestTheRingSaysWhichSourceItCounted:
    """Puck's first hard condition from v2.2, unchanged and still met -- with a
    different answer, because the per-drug hop is gone."""

    def test_every_state_names_the_source(self) -> None:
        cells = [
            clinical_activity_cell(rec(("A", live_n(3)))),
            clinical_activity_cell(rec(("A", stopped_n(2, categories=["Negative"])))),
            clinical_activity_cell(rec(("A", stopped_n(2, categories=["Study_Design"])))),
            clinical_activity_cell(rec(("A", [report(status="COMPLETED")]))),
        ]
        for c in cells:
            assert SOURCE_LABEL in c.detail, c.detail

    def test_it_says_there_is_no_per_drug_hop(self) -> None:
        c = clinical_activity_cell(rec(("A", live_n(1))))
        assert "no per-drug hop" in c.detail

    def test_the_ring_label_carries_the_source(self) -> None:
        """The figure's own key and legend, not just the caption."""
        from targetring.figure import RING_SPECS
        spec = next(r for r in RING_SPECS if r.key == "clinical_activity")
        assert SOURCE_LABEL in spec.label


class TestTheStopStateIsCategorisedAgain:
    """v2.4 item 2. v2.2 downgraded -1.0 to "stopped early -- cause NOT
    categorised" because ClinicalTrials.gov gives only free text. Open Targets
    categorises the same stops, and that distinction is the entire reason this
    is a second ring: COUNCIL D4 measured that ~7 of 8 terminations say nothing
    about the target."""

    def test_a_for_cause_stop_is_minus_one(self) -> None:
        c = clinical_activity_cell(
            rec(("A", stopped_n(3, categories=["Safety_Sideeffects"]))))
        assert c.value == -1.0
        assert "stopped for cause" in c.detail
        assert "Safety_Sideeffects" in c.detail

    def test_an_administrative_stop_is_not(self) -> None:
        """The seven of eight. Insufficient_Enrollment says nothing about the
        target and must not render as a target-level negative."""
        c = clinical_activity_cell(
            rec(("A", stopped_n(8, categories=["Insufficient_Enrollment"]))))
        assert c.value == 0.0
        assert "NONE for cause" in c.detail
        assert "Insufficient_Enrollment 8" in c.detail

    def test_the_two_are_separated_within_one_target(self) -> None:
        r = rec(("A", stopped_n(7, categories=["Insufficient_Enrollment"])
                 + stopped_n(1, first=50, categories=["Negative"])))
        c = clinical_activity_cell(r)
        assert c.value == -1.0
        assert "1 of 8" in c.detail

    def test_an_uncategorised_stop_is_not_for_cause(self) -> None:
        """`Invalid_Reason` / `No_Context` / `Uncategorised` exist and assert
        nothing; a row carrying only one of them is categorised in name only."""
        c = clinical_activity_cell(rec(("A", stopped_n(4, categories=["Uncategorised"]))))
        assert c.value == 0.0

    def test_the_legend_label_says_for_cause_again(self) -> None:
        from targetring.figure import ACTIVITY_LABELS
        assert "stopped for cause" in ACTIVITY_LABELS[-1.0]
        assert "NOT categorised" not in ACTIVITY_LABELS[-1.0]

    def test_the_free_text_still_reaches_the_drawer_verbatim(self) -> None:
        """AGENTS.md rule 5: it is data, shown, never interpreted -- and now it
        is shown *beside* the category rather than instead of one."""
        d = trials_panel(rec(("A", stopped_n(1, categories=["Negative"]))))
        row = d["drugs"][0]["stopped"][0]
        assert row["why_stopped"] == "slow accrual led to early closure."
        assert row["categories"] == ["Negative"] and row["for_cause"] is True

    def test_the_for_cause_count_reaches_the_panel(self) -> None:
        d = trials_panel(rec(("A", stopped_n(3, categories=["Insufficient_Enrollment"])
                              + stopped_n(2, first=40, categories=["Negative"]))))
        assert d["n_pairs_for_cause"] == 2
        assert d["stop_categories"]["Insufficient_Enrollment"] == 3
        assert "categorised FOR CAUSE" in d["dedup_note"]


class TestSponsorAndLiveStatusReachTheDrawer:
    """Puck's second hard condition: that is what tells the user a target is
    crowded."""

    def test_lead_sponsors_are_named(self) -> None:
        d = trials_panel(rec(
            ("A", [report("nct00000001", sponsor="Bayer"),
                   report("nct00000002", sponsor="NCI")]),
            ("B", [report("nct00000003", sponsor="Roche")]),
        ))
        assert d["n_lead_sponsors"] == 3
        assert d["lead_sponsors"], "the crowding signal must be named, not counted"

    def test_live_rows_carry_status_and_sponsor(self) -> None:
        d = trials_panel(rec(("A", live_n(2))))
        row = d["drugs"][0]
        assert row["n_live"] == 2
        assert row["live"][0]["status"] in LIVE_STATUSES
        assert row["live"][0]["sponsor"]

    def test_the_ring_detail_also_counts_the_live_rows(self) -> None:
        c = clinical_activity_cell(rec(("A", live_n(2))))
        assert "2 of 2 trial row(s) recruiting" in c.detail

    def test_an_nct_id_becomes_a_link_without_a_request(self) -> None:
        d = trials_panel(rec(("A", live_n(1))))
        assert d["drugs"][0]["live"][0]["url"] == (
            "https://clinicaltrials.gov/study/NCT00000000")


class TestDrawerCountsAreDistinctNCTIds:
    """v2.3 item 2, kept. The per-drug counts are drug-trial pairs, so summing
    them over a target's drugs double-counts any trial testing two of them."""

    def test_two_drugs_in_the_same_trials_count_once(self) -> None:
        d = trials_panel(rec(("A", live_n(3)), ("B", live_n(3))))
        assert d["n_pairs_live"] == 6
        assert d["n_distinct_live"] == 3
        assert d["n_pairs"] == 6 and d["n_distinct_trials"] == 3

    def test_disjoint_trials_are_not_collapsed(self) -> None:
        d = trials_panel(rec(("A", live_n(3)), ("B", live_n(3, first=100))))
        assert d["n_pairs_live"] == d["n_distinct_live"] == 6

    def test_partial_overlap_lands_between_the_two(self) -> None:
        d = trials_panel(rec(("A", live_n(4)), ("B", live_n(4, first=2))))
        assert d["n_pairs_live"] == 8
        assert d["n_distinct_live"] == 6

    def test_a_repeat_within_one_drug_is_caught(self) -> None:
        """Unlike the retired per-drug hop, one drug row CAN repeat an id, so
        `n_distinct` is a real check rather than a restatement of the count."""
        d = trials_panel(rec(("A", live_n(3) + live_n(1))))
        row = d["drugs"][0]
        assert row["n_trials"] == 4 and row["n_distinct"] == 3

    def test_a_trial_live_for_one_drug_and_stopped_for_another_is_reported(self) -> None:
        """Not resolved -- picking a winner is a judgement this build cannot make."""
        d = trials_panel(rec(
            ("A", [report("nct00000001"), report("nct00000002")]),
            ("B", [report("nct00000001", status="TERMINATED"),
                   report("nct00000002", status="TERMINATED")]),
        ))
        assert d["n_live_and_stopped"] == 2
        assert "live for one drug and stopped for another" in d["dedup_note"]

    def test_the_panel_says_which_number_is_which(self) -> None:
        """It changed numbers on a panel that had already been read."""
        d = trials_panel(rec(("A", live_n(3)), ("B", live_n(3))))
        note = d["dedup_note"]
        assert "DISTINCT NCT IDS" in note
        assert "6 drug-trial PAIRS" in note and "3 DISTINCT" in note

    def test_a_status_row_with_no_nct_id_makes_the_distinct_count_a_floor(self) -> None:
        """It is in the pair count and cannot be in the distinct one."""
        d = trials_panel(rec(("A", live_n(2) + [report("eme99", status="RECRUITING")])))
        assert d["n_pairs_live"] == 3 and d["n_distinct_live"] == 2
        assert d["distinct_is_a_floor"] is True
        assert "FLOOR" in d["dedup_note"]

    def test_the_ring_reports_the_distinct_total_too(self) -> None:
        c = clinical_activity_cell(rec(("A", live_n(3)), ("B", live_n(3))))
        assert c.raw["n_distinct_trials"] == 3
        assert "3 distinct NCT id(s)" in c.detail


class TestTheOtherStates:
    def test_no_drugs_is_not_applicable_not_no_data(self) -> None:
        assert clinical_activity_cell(rec(count=0)).state == "not_applicable"

    def test_a_gene_open_targets_does_not_hold_is_no_data(self) -> None:
        assert clinical_activity_cell(
            OTRecord("ENSG00000999999", found=False)).state == "no_data"

    def test_trials_but_none_live_and_none_stopped_is_dormant(self) -> None:
        c = clinical_activity_cell(rec(("A", [report(status="COMPLETED")] * 6)))
        assert c.value == 0.0
        assert "dormant" in c.detail

    def test_a_live_trial_outranks_a_for_cause_stop(self) -> None:
        """Precedence unchanged from v1: for-cause-first labels BRAF "stopped"
        while ten of its eighteen agents are recruiting."""
        c = clinical_activity_cell(
            rec(("A", live_n(1) + stopped_n(9, categories=["Negative"]))))
        assert c.value == 1.0
        assert "9 stopped for cause" in c.detail


class TestStatusClassificationIsExhaustive:
    def test_the_three_status_sets_do_not_overlap(self) -> None:
        sets = [set(LIVE_STATUSES), set(STOPPED_STATUSES), set(INERT_STATUSES)]
        for a, b in ((0, 1), (0, 2), (1, 2)):
            assert not sets[a] & sets[b]

    def test_each_stated_status_lands_in_its_stated_bucket(self) -> None:
        for s in LIVE_STATUSES:
            assert classify_status(s) == "live"
        for s in STOPPED_STATUSES:
            assert classify_status(s) == "stopped"
        for s in INERT_STATUSES:
            assert classify_status(s) == "inert"

    def test_an_unknown_status_is_a_hard_failure_not_a_silent_drop(self) -> None:
        """A status in neither bucket would be counted in neither, which on a
        three-state ring reads as 'dormant'."""
        c = clinical_activity_cell(rec(("A", [report(status="SOMETHING_NEW")])))
        assert c.state == "failed"
        assert "SOMETHING_NEW" in c.detail

    def test_one_unknown_status_fails_the_cell_even_beside_good_rows(self) -> None:
        c = clinical_activity_cell(
            rec(("A", live_n(5) + [report("nct00000077", status="NEW_THING")])))
        assert c.state == "failed", "a live count over an unclassified set is not a count"

    def test_the_for_cause_set_is_stated_not_inferred(self) -> None:
        assert FOR_CAUSE_CATEGORIES == {"Negative", "Safety_Sideeffects"}


class TestNCTIdParsing:
    def test_open_targets_lower_case_ids_are_normalised(self) -> None:
        """Mixed case inside one distinct count would double-count a trial."""
        assert nct_id({"id": "nct00405587"}) == "NCT00405587"
        assert nct_id({"id": "NCT00405587"}) == "NCT00405587"

    def test_another_registrys_id_is_not_an_nct_id(self) -> None:
        """BRAF's 1,433 reports carry 1,381 NCT ids and 52 others: EMA `eme…`,
        ATC `l01…`, DailyMed set ids."""
        for other in ("eme123", "l01xe", "d05abc", "", None):
            assert nct_id({"id": other}) is None


class TestTheDrugTableAgrees:
    """`drug_pipeline_rows` feeds the same drawer and read the same AACT literal."""

    def test_the_drug_row_counts_status_rows_without_a_source_literal(self) -> None:
        r = rec(("A", [report("nct00000001", source="ClinicalTrials.gov"),
                       {"id": "t1", "source": "TTD", "trialOverallStatus": None}]))
        row = drug_pipeline_rows(r)[0]
        assert row["n_trials"] == 2, "every report, including the label row"
        assert row["n_with_status"] == 1, "only the one that is a trial"

    def test_stop_reasons_reach_the_drug_row(self) -> None:
        r = rec(("A", stopped_n(2, categories=["Negative"])))
        assert drug_pipeline_rows(r)[0]["stop_reasons"] == {"Negative": 2}


@pytest.mark.live
class TestTheLiveShapeIsWhatThisBuildParses:
    """The measurements in `docs/V2-REPORT.md` §v2.4, re-asserted live. If Open
    Targets relabels `source` again these still pass, which is the point; if it
    drops a field this build reads, they fail loudly."""

    @pytest.mark.asyncio
    async def test_braf_carries_trials_with_ids_sponsors_and_categories(self) -> None:
        from targetring.adapters.opentargets import OpenTargetsAdapter
        from targetring.cache import Cache
        from targetring.http import Http

        async with Http() as http:
            recs = await OpenTargetsAdapter(http, Cache(enabled=False)).fetch(
                ["ENSG00000157764"])
        rows = trial_reports(recs["ENSG00000157764"])
        assert len(rows) > 1000, f"only {len(rows)} trial rows on BRAF"
        assert sum(1 for r in rows if r["nct_id"]) > 1000
        assert sum(1 for r in rows if r["sponsor"]) > 1000
        assert sum(1 for r in rows if r["stop_categories"]) > 100, (
            "Open Targets categorises stop reasons; that is the whole reason the "
            "-1.0 state can say 'for cause' again")
        assert not [r for r in rows if r["bucket"] is None]

    @pytest.mark.asyncio
    async def test_the_seven_of_eight_measurement_still_holds(self) -> None:
        """COUNCIL D4: most terminations say nothing about the target. If this
        ever inverts, the case for two rings rather than one has changed."""
        from targetring.adapters.opentargets import OpenTargetsAdapter
        from targetring.cache import Cache
        from targetring.http import Http

        async with Http() as http:
            recs = await OpenTargetsAdapter(http, Cache(enabled=False)).fetch(
                ["ENSG00000157764"])
        rows = [r for r in trial_reports(recs["ENSG00000157764"]) if r["stop_categories"]]
        for_cause = sum(1 for r in rows if r["for_cause"])
        assert rows and for_cause / len(rows) < 0.25, (
            f"{for_cause} of {len(rows)} categorised stops are for cause")
