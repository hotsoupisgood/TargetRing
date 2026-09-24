"""v2.2 item 2 — the activity ring, re-sourced to ClinicalTrials.gov v2.

Open Targets stopped emitting AACT on 2026-09-24 and the ring went dark for
every gene (`docs/V2-REPORT.md` §v2.1, Newly open 2). Puck's decision was to hop
to ClinicalTrials.gov per drug, with two hard conditions: the ring must state
which source it counted, on the figure, because CT.gov is a different population
from AACT; and sponsor plus live status must reach the drawer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from targetring.adapters.clinicaltrials import (
    CTGOV_PAGE_SIZE,
    INERT_STATUSES,
    LIVE_STATUSES,
    SOURCE_LABEL,
    STOPPED_STATUSES,
    CTDrug,
    CTGovAdapter,
    activity_cell,
    drawer_trials,
    intervention_query,
    summarise,
)
from targetring.cache import Cache
from targetring.config import CTGOV_STUDIES
from targetring.http import HardFailure, Http, RateLimited


def drug(name="DRUGA", *, live=0, stopped=0, total=None, error=None, complete=True,
         first=0) -> CTDrug:
    """One synthetic CT.gov answer.

    `first` shifts the NCT ids, so two drugs can be given overlapping or
    disjoint trial sets — which is what v2.3 item 2's de-duplication is about.
    Left at 0 the ids are identical between drugs, i.e. total overlap.
    """
    rows_live = [{"nct_id": f"NCT{i:08d}", "status": "RECRUITING",
                  "sponsor": f"Sponsor {i}", "conditions": ["X"], "phases": ["PHASE2"],
                  "why_stopped": None, "url": None} for i in range(first, first + live)]
    rows_stopped = [{"nct_id": f"NCT9{i:07d}", "status": "TERMINATED",
                     "sponsor": "Stopper Inc", "conditions": ["X"], "phases": ["PHASE1"],
                     "why_stopped": "slow accrual led to early closure.", "url": None}
                    for i in range(first, first + stopped)]
    return CTDrug(
        name, f'"{name}"',
        n_total=(live + stopped if total is None else total) if error is None else None,
        n_live=live if error is None else None,
        n_stopped=stopped if error is None else None,
        live_rows=rows_live, stopped_rows=rows_stopped, error=error, complete=complete,
        nct_ids=[r["nct_id"] for r in rows_live + rows_stopped],
        live_ids=[r["nct_id"] for r in rows_live],
        stopped_ids=[r["nct_id"] for r in rows_stopped],
    )


class TestTheRingSaysWhichSourceItCounted:
    """Puck's first hard condition. AACT and CT.gov are different populations
    and the numbers are not continuous with yesterday's."""

    def test_every_state_names_the_source(self) -> None:
        cells = [
            activity_cell(["A"], {"A": drug("A", live=3)}, drug_count=1),
            activity_cell(["A"], {"A": drug("A", stopped=2)}, drug_count=1),
            activity_cell(["A"], {"A": drug("A", total=4)}, drug_count=1),
            activity_cell(["A"], {"A": drug("A", total=0)}, drug_count=1),
            activity_cell([], {}, drug_count=0),
        ]
        for c in cells:
            assert SOURCE_LABEL in c.detail, c.detail

    def test_it_says_it_is_not_aact(self) -> None:
        c = activity_cell(["A"], {"A": drug("A", live=1)}, drug_count=1)
        assert "AACT" in c.detail and "not continuous" in c.detail

    def test_the_ring_label_carries_the_source(self) -> None:
        """The figure's own key and legend, not just the caption."""
        from targetring.figure import RING_SPECS
        spec = next(r for r in RING_SPECS if r.key == "clinical_activity")
        assert SOURCE_LABEL in spec.label


class TestTheStopStateDoesNotClaimACause:
    """COUNCIL D4: only ~13% of terminations say anything about the target, and
    CT.gov's `whyStopped` is free text with no category. The AACT ring could say
    "stopped for cause" because Open Targets categorised it. This one cannot."""

    def test_the_detail_says_the_cause_is_not_categorised(self) -> None:
        c = activity_cell(["A"], {"A": drug("A", stopped=3)}, drug_count=1)
        assert c.value == -1.0
        assert "NOT categorise" in c.detail
        assert "for cause" not in c.detail

    def test_the_legend_label_says_so_too(self) -> None:
        from targetring.figure import ACTIVITY_LABELS
        assert "NOT categorised" in ACTIVITY_LABELS[-1.0]
        assert "for cause" not in ACTIVITY_LABELS[-1.0]

    def test_the_free_text_reaches_the_drawer_verbatim(self) -> None:
        """AGENTS.md rule 5: it is data, shown, never interpreted."""
        d = drawer_trials(["A"], {"A": drug("A", stopped=1)})
        assert d["drugs"][0]["stopped"][0]["why_stopped"] == (
            "slow accrual led to early closure.")
        assert "NOT categorised" in d["note"]


class TestSponsorAndLiveStatusReachTheDrawer:
    """Puck's second hard condition: that is what tells the user a target is
    crowded."""

    def test_lead_sponsors_are_named(self) -> None:
        d = drawer_trials(["A", "B"], {"A": drug("A", live=3), "B": drug("B", live=2)})
        assert d["n_lead_sponsors"] >= 3
        assert d["lead_sponsors"], "the crowding signal must be named, not counted"

    def test_live_rows_carry_status_and_sponsor(self) -> None:
        d = drawer_trials(["A"], {"A": drug("A", live=2)})
        row = d["drugs"][0]
        assert row["n_live"] == 2
        assert row["live"][0]["status"] in LIVE_STATUSES
        assert row["live"][0]["sponsor"]

    def test_the_ring_detail_also_names_sponsors(self) -> None:
        c = activity_cell(["A"], {"A": drug("A", live=2)}, drug_count=1)
        assert "lead sponsor" in c.detail and "Sponsor 0" in c.detail


class TestDrawerCountsAreDistinctNCTIds:
    """v2.3 item 2. The per-drug counts are drug-trial pairs, so summing them
    over a target's drugs double-counts any trial testing two of them --
    docs/V2-REPORT.md §v2.2, *Still open* 4. The ids come off the same page the
    counts do, so the fix costs no extra request."""

    def test_two_drugs_in_the_same_trials_count_once(self) -> None:
        # Identical id sets: 3 live pairs + 3 live pairs, 3 distinct trials.
        d = drawer_trials(["A", "B"], {"A": drug("A", live=3), "B": drug("B", live=3)})
        assert d["n_pairs_live"] == 6
        assert d["n_distinct_live"] == 3
        assert d["n_pairs"] == 6 and d["n_distinct_trials"] == 3

    def test_disjoint_trials_are_not_collapsed(self) -> None:
        d = drawer_trials(["A", "B"],
                          {"A": drug("A", live=3), "B": drug("B", live=3, first=100)})
        assert d["n_pairs_live"] == d["n_distinct_live"] == 6

    def test_partial_overlap_lands_between_the_two(self) -> None:
        d = drawer_trials(["A", "B"],
                          {"A": drug("A", live=4), "B": drug("B", live=4, first=2)})
        assert d["n_pairs_live"] == 8
        assert d["n_distinct_live"] == 6

    def test_per_drug_rows_are_unchanged(self) -> None:
        """One drug's query returns one row per study, so its own counts were
        never inflated. Checked rather than assumed."""
        d = drawer_trials(["A", "B"], {"A": drug("A", live=3), "B": drug("B", live=3)})
        for row in d["drugs"]:
            assert row["n_distinct"] == row["n_trials"]
            assert row["n_distinct_live"] == row["n_live"]

    def test_a_trial_live_for_one_drug_and_stopped_for_another_is_reported(self) -> None:
        """Not resolved — picking a winner is a judgement this build cannot make."""
        a = drug("A", live=2)
        b = drug("B", stopped=2)
        b.stopped_ids = list(a.live_ids)
        d = drawer_trials(["A", "B"], {"A": a, "B": b})
        assert d["n_live_and_stopped"] == 2
        assert "live for one drug and stopped for another" in d["dedup_note"]

    def test_the_panel_says_which_number_is_which(self) -> None:
        """It changes numbers on a panel that has already been read."""
        d = drawer_trials(["A", "B"], {"A": drug("A", live=3), "B": drug("B", live=3)})
        note = d["dedup_note"]
        assert "DISTINCT NCT IDS" in note
        assert "6 drug-trial PAIRS" in note and "3 DISTINCT" in note
        assert "Per-drug rows are unchanged" in note

    def test_a_truncated_page_makes_the_distinct_count_a_floor(self) -> None:
        d = drawer_trials(["A"], {"A": drug("A", live=2, total=9000, complete=False)})
        assert d["distinct_is_a_floor"] is True
        assert "FLOOR" in d["dedup_note"]

    def test_an_unanswered_drug_makes_it_a_floor_too(self) -> None:
        d = drawer_trials(["A", "B"],
                          {"A": drug("A", live=2), "B": drug("B", error="boom")})
        assert d["distinct_is_a_floor"] is True
        assert "did not answer" in d["dedup_note"]


class TestPartialAnswersResolveOneWay:
    """One live trial is positive, monotone evidence and survives a failure
    elsewhere. "Dormant" and "stopped" are claims about everything, and do not.
    """

    def test_a_live_trial_survives_a_failed_sibling_hop(self) -> None:
        c = activity_cell(["A", "B"],
                          {"A": drug("A", live=1), "B": drug("B", error="Timeout")},
                          drug_count=2)
        assert c.value == 1.0
        assert "did not answer" in c.detail

    def test_no_live_trial_plus_a_failed_hop_is_failed_not_dormant(self) -> None:
        c = activity_cell(["A", "B"],
                          {"A": drug("A", total=4), "B": drug("B", error="Timeout")},
                          drug_count=2)
        assert c.state == "failed"
        assert "unknown rather than either" in c.detail

    def test_a_truncated_page_cannot_establish_dormancy(self) -> None:
        """A floor count can prove a live trial and can never disprove one."""
        c = activity_cell(["A"], {"A": drug("A", total=5000, complete=False)},
                          drug_count=1)
        assert c.state == "failed"
        assert str(CTGOV_PAGE_SIZE) in c.detail

    def test_a_truncated_page_still_establishes_a_live_trial(self) -> None:
        c = activity_cell(["A"], {"A": drug("A", live=2, total=5000, complete=False)},
                          drug_count=1)
        assert c.value == 1.0
        assert "floor" in c.detail


class TestTheOtherStates:
    def test_no_drugs_is_not_applicable_not_no_data(self) -> None:
        assert activity_cell([], {}, drug_count=0).state == "not_applicable"

    def test_ct_gov_knowing_no_such_drug_is_no_data_not_dormant(self) -> None:
        """OT holds the drug; CT.gov's intervention search does not match the
        name. That is absence of trial data, not an absence of trials."""
        c = activity_cell(["A"], {"A": drug("A", total=0)}, drug_count=1)
        assert c.state == "no_data"
        assert "does not match the name" in c.detail

    def test_trials_but_none_live_and_none_stopped_is_dormant(self) -> None:
        c = activity_cell(["A"], {"A": drug("A", total=6)}, drug_count=1)
        assert c.value == 0.0

    def test_the_hop_budget_is_absent_not_partial(self) -> None:
        c = activity_cell(["A"], {}, drug_count=1, budget_exhausted=True)
        assert c.state == "no_data"
        assert "budget" in c.detail


class TestTheQueryForm:
    """CT.gov concept-expands and the expansion is large, so the form sent is a
    decision and the string sent is recorded."""

    def test_a_multiword_name_is_a_phrase_not_an_and(self) -> None:
        assert intervention_query("SORAFENIB TOSYLATE") == '"SORAFENIB TOSYLATE"'

    def test_an_empty_name_is_refused_rather_than_sent(self) -> None:
        with pytest.raises(HardFailure):
            intervention_query("   ")

    def test_the_query_travels_with_the_record(self) -> None:
        d = drawer_trials(["A"], {"A": drug("A", live=1)})
        assert d["drugs"][0]["query"] == '"A"'


class TestStatusClassificationIsExhaustive:
    def test_the_three_status_sets_do_not_overlap(self) -> None:
        sets = [set(LIVE_STATUSES), set(STOPPED_STATUSES), set(INERT_STATUSES)]
        for a, b in ((0, 1), (0, 2), (1, 2)):
            assert not sets[a] & sets[b]

    def test_summarise_splits_on_the_stated_sets(self) -> None:
        rows = ([{"status": s, "sponsor": None} for s in LIVE_STATUSES]
                + [{"status": s, "sponsor": None} for s in STOPPED_STATUSES]
                + [{"status": "COMPLETED", "sponsor": None}])
        out = summarise(rows)
        assert out["n_live"] == len(LIVE_STATUSES)
        assert out["n_stopped"] == len(STOPPED_STATUSES)

    @pytest.mark.asyncio
    async def test_an_unknown_status_is_a_hard_failure_not_a_silent_drop(self) -> None:
        """A status in neither bucket would be counted in neither, which on a
        three-state ring reads as 'dormant'."""
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "totalCount": 1,
                "studies": [{"protocolSection": {
                    "identificationModule": {"nctId": "NCT1"},
                    "statusModule": {"overallStatus": "SOMETHING_NEW"}}}],
            }, headers={"content-type": "application/json"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rec = await CTGovAdapter(Http(client), Cache(enabled=False))._one("X")
        assert rec.error and "does not classify" in rec.error
        assert "SOMETHING_NEW" in rec.error

    @pytest.mark.asyncio
    async def test_a_page_bigger_than_the_stated_total_is_refused(self) -> None:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "totalCount": 1,
                "studies": [{"protocolSection": {
                    "identificationModule": {"nctId": f"NCT{i}"},
                    "statusModule": {"overallStatus": "COMPLETED"}}} for i in range(3)],
            }, headers={"content-type": "application/json"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rec = await CTGovAdapter(Http(client), Cache(enabled=False))._one("X")
        assert rec.error and "disagree" in rec.error


class TestRateLimitIsAHardFailure:
    """AGENTS.md rule 9, and it was not hypothetical: a cold council run at 4
    concurrent hops got HTTP 429 from clinicaltrials.gov after 12 s. The run
    failed, nothing was retried, and the adapter now makes one call per drug at
    a lower concurrency."""

    @pytest.mark.asyncio
    async def test_a_429_propagates_rather_than_becoming_a_failed_cell(self) -> None:
        import httpx

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return httpx.Response(429, text="slow down")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RateLimited):
            await CTGovAdapter(Http(client), Cache(enabled=False)).fetch_many(["A"])
        assert len(calls) == 1, "a 429 must never be retried"

    def test_this_host_is_throttled_below_the_ceiling(self) -> None:
        from targetring.config import MAX_CONCURRENCY_PER_HOST
        from targetring.http import HOST_CONCURRENCY, HOST_PACING_SECONDS
        host = "clinicaltrials.gov"
        assert HOST_CONCURRENCY[host] <= MAX_CONCURRENCY_PER_HOST
        assert HOST_PACING_SECONDS[host] > 0


class TestFailuresAreNeverCached:
    @pytest.mark.asyncio
    async def test_a_failed_hop_leaves_nothing_behind(self, tmp_path: Path) -> None:
        import httpx

        cache = Cache(root=tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="nope")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rec = await CTGovAdapter(Http(client), cache)._one("X")
        assert rec.error
        assert cache.get("clinicaltrials.studies", '"X"') is None


@pytest.mark.live
class TestTheCouncilsOwnEvidenceStillHolds:
    """COUNCIL D3's measurements, re-asserted against the live API."""

    @pytest.mark.asyncio
    async def test_the_endpoint_and_the_field_list_still_work(self) -> None:
        from targetring.adapters.clinicaltrials import CTGOV_FIELDS
        from targetring.http import json_or_raise
        async with Http() as http:
            r = await http.get(CTGOV_STUDIES, {
                "query.intr": '"VEMURAFENIB"', "fields": CTGOV_FIELDS,
                "pageSize": 2, "countTotal": "true"})
            body = json_or_raise(r)
        assert isinstance(body.get("totalCount"), int) and body["totalCount"] > 50
        p = body["studies"][0]["protocolSection"]
        assert p["identificationModule"]["nctId"].startswith("NCT")
        assert p["statusModule"]["overallStatus"]
        assert p["sponsorCollaboratorsModule"]["leadSponsor"]["name"]

    @pytest.mark.asyncio
    async def test_an_unknown_parameter_is_rejected_not_ignored(self) -> None:
        """The ChEMBL hazard, checked on this host: ChEMBL answers 200 with the
        whole unfiltered table. CT.gov 400s, which is the safe behaviour, and
        this test is what would notice if that ever changed."""
        async with Http() as http:
            with pytest.raises(HardFailure) as e:
                await http.get(CTGOV_STUDIES, {"query.intr": '"VEMURAFENIB"',
                                               "notaparam": "x", "pageSize": 1})
        assert "400" in str(e.value)
