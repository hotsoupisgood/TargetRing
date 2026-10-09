"""Rings 1 and 2 stop calling an Atlas contrast "yours" — `c24c8ec` part 2.

Part 1 landed `sources.DESource` and threaded it through nine modules, and its
own commit message says why it shipped without these: *"Committed before its
tests so the work is recoverable — this diff had already survived two sessions
uncommitted. Part 2 adds the regression tests that fail on the constants and
pass on DESource, and covers `Accounting.n_up`/`n_down`."* This is part 2.

THE DEFECT, AND WHERE IT STILL IS. Rings 1 and 2 were described by module-level
constants written for the paste path — `RingSpec("logfc", "logFC (yours)", …)`
and `UNLINKED_RINGS["logfc"] = "…the DE table you submitted — your own
measurement, read straight off your file"`. On a contrast run every word is
false, and because they were constants the same falsehood reached six surfaces
from one root cause.

The three contrast runs under `runs/` were written on 2026-10-03, four days
before the fix, so their artefacts are the reproduction preserved on disk:
`TestTheDefectIsFrozenOnDisk` reads their `manifest.json` and finds Atlas genes
described as Puck's own submitted table. Those bytes cannot be corrected
retroactively and the tests say so rather than passing over it.

What CAN be checked is that everything resolved at read time is right now, which
is what `DESource.from_manifest` is for: a run loaded off disk must reach the
same answer as the run that wrote it, from the artefact rather than from process
state. `TestEverySurfaceResolvesFromTheRun` is that check, on those same three
runs.

One surface is deliberately NOT asserted clean, and `TestTheLegacyLabelIsADecisionNotABug`
records why: `citations.citation_rows` prints "the run's own label, as the
renderer wrote it" by documented intent (PANEL P1, and `column_label`'s
docstring gives the argument). On these three runs that puts "logFC (yours)" on
the same row as `source_name` "Expression Atlas" and a reason beginning "Not a
table you submitted". Two correct principles collide and only Puck can pick;
the behaviour is pinned here so a change to it is deliberate.

No network and no analysis (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from targetring import citations as citations_mod
from targetring import sources as S
from targetring import view as view_mod
from targetring.contrast import AnalyticsRow, top_n_by_padj
from targetring.figure import ring_specs_for

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"

#: The strings that are true of a pasted table and false of a contrast. Matched
#: case-insensitively; "yours" is matched as a whole word so "your own" does not
#: hide behind it.
PASTE_CLAIMS = ("you submitted", "your submitted", "your own measurement",
                "read straight off your file", "(yours)")


def _manifest(run_id: str) -> dict:
    return json.loads((RUNS / run_id / "manifest.json").read_text())


def contrast_runs() -> list[str]:
    """Every run on disk whose genes came from an Atlas contrast."""
    out = []
    for d in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        m = d / "manifest.json"
        if not m.exists():
            continue
        try:
            j = json.loads(m.read_text())
        except Exception:  # noqa: BLE001 -- a half-written run is not this test's
            continue
        if (j.get("contrast") or {}).get("accession"):
            out.append(d.name)
    return out


CONTRAST_RUNS = contrast_runs()
pytestmark = pytest.mark.skipif(not CONTRAST_RUNS,
                                reason="no contrast run on disk to audit")


# ===========================================================================


class TestTheDefectIsFrozenOnDisk:
    """The reproduction. These artefacts were written before the fix and say
    something false; a test that could not see that would not be testing the
    defect, only the current code path."""

    def test_there_are_contrast_runs_to_audit(self) -> None:
        assert CONTRAST_RUNS, "the audit read nothing"

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_frozen_ring_sources_describe_atlas_genes_as_submitted(
            self, run_id) -> None:
        """What a reader of `manifest.json` alone is told. If this ever starts
        failing, runs written after `c24c8ec` are the only ones left on disk and
        this class can go — it is a record of a fixed defect, not a requirement
        that it stay broken."""
        m = _manifest(run_id)
        rows = {r.get("ring"): (r.get("reason") or "")
                for r in (m.get("ring_sources") or [])}
        for ring in ("logfc", "neg_log10_padj"):
            reason = rows.get(ring, "").lower()
            if not reason:
                continue
            assert any(c in reason for c in PASTE_CLAIMS), (
                f"{run_id}/{ring} no longer carries the old text — if this run "
                f"was rewritten after c24c8ec, drop it from this class")

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_frozen_ordering_note_still_says_submitted(self, run_id) -> None:
        """The stored sentence, which is what a reader of `manifest.json` alone
        is told. It is no longer what the app prints — see
        `TestTheOrderingNoteIsResolvedNotReadBack` — but the bytes are still
        there and the surface is required to account for them."""
        note = (_manifest(run_id).get("ordering_note") or "").lower()
        if not note:
            pytest.skip(f"{run_id} records no ordering note")
        assert "submitted" in note, note
        # and the run it describes is demonstrably not a submitted table
        assert _manifest(run_id)["contrast"]["accession"]


class TestTheOrderingNoteIsResolvedNotReadBack:
    """`table_payload` used to return `ordering_note` straight out of the
    manifest, so the Table tab printed "spoke order: the order of your submitted
    DE table" for genes nobody submitted -- the last piece of this defect still
    visible in the running app. It is re-resolved from the run's own `contrast`
    block now.

    Why this string and not a ring label: a label is a record of the text
    painted on that figure (PANEL P1), and replacing it would make the strip
    disagree with the artefact. This is a **claim about where the genes came
    from**, which the same manifest contains the evidence to settle two keys
    away. See `view._ordering_note`.
    """

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_note_names_the_contrast_and_not_your_table(self, run_id
                                                             ) -> None:
        p = view_mod.table_payload(run_id)
        if not p["ordering_note"]:
            pytest.skip(f"{run_id} records no ordering")
        c = _manifest(run_id)["contrast"]
        assert "submitted DE table" not in p["ordering_note"]
        assert c["accession"] in p["ordering_note"]
        assert c["contrast_id"] in p["ordering_note"]

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_stored_sentence_is_carried_and_the_drift_is_stated(
            self, run_id) -> None:
        """`citations._drift`'s rule -- said, not resolved silently. A reader
        with this run's manifest.json open must be told why the two disagree
        rather than finding it."""
        p = view_mod.table_payload(run_id)
        if not p["ordering_note_recorded"]:
            pytest.skip(f"{run_id} records no ordering note")
        assert p["ordering_note_recorded"] != p["ordering_note"]
        assert p["ordering_note_recorded"] in p["ordering_note_drift"]
        assert "manifest recorded" in p["ordering_note_drift"]

    def test_a_paste_run_is_untouched_and_claims_no_drift(self) -> None:
        """The blast radius. Measured across every run on disk: 3 change, 162
        resolve to the identical sentence, 91 have none either way."""
        changed = []
        for d in sorted(p for p in RUNS.iterdir() if p.is_dir()):
            if not (d / "manifest.json").exists():
                continue
            m = json.loads((d / "manifest.json").read_text())
            if (m.get("contrast") or {}).get("accession"):
                continue
            try:
                p = view_mod.table_payload(d.name)
            except Exception:  # noqa: BLE001 -- unloadable runs are not this test's
                continue
            stored = (m.get("ordering_note") or "").strip()
            if p["ordering_note"] != stored:
                changed.append((d.name, stored, p["ordering_note"]))
            assert not p["ordering_note_drift"], d.name
        assert not changed, changed[:3]

    def test_a_run_that_records_no_ordering_keeps_its_stored_sentence(self
                                                                      ) -> None:
        """Nothing to resolve from, so nothing is invented: the stored string is
        returned rather than replaced by a guess."""
        out = view_mod._ordering_note(
            {"ordering_note": "spoke order: something a later build forgot"},
            {}, S.DESource.pasted())
        assert out["ordering_note"] == "spoke order: something a later build forgot"
        assert out["ordering_note_drift"] == ""


class TestEverySurfaceResolvesFromTheRun:
    """Everything decided at READ time must now say Atlas, for these very runs."""

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_source_resolves_to_the_contrast_not_the_paste_default(
            self, run_id) -> None:
        de = S.DESource.from_manifest(_manifest(run_id))
        assert de.kind == "contrast"
        assert de.short_source == "Expression Atlas"
        assert de.label_qualifier == "Atlas"
        c = _manifest(run_id)["contrast"]
        assert c["accession"] in de.source_phrase
        assert c["contrast_id"] in de.source_phrase

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_figure_labels_say_atlas_and_not_yours(self, run_id) -> None:
        de = S.DESource.from_manifest(_manifest(run_id))
        specs = {s.key: s for s in ring_specs_for(de)}
        for ring in ("logfc", "neg_log10_padj"):
            spec = specs[ring]
            assert "(Atlas)" in spec.label, spec.label
            assert "(yours)" not in spec.label, spec.label
            assert spec.short_source == "Expression Atlas"

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_stated_reasons_name_the_file_and_deny_the_claim(
            self, run_id) -> None:
        """The reason a reader checks the number against. It must name the
        analytics file, and it must positively say the table was not submitted —
        the old text's forwarding pointer (`input.columns.logfc`) pointed at a
        manifest key that exists on no run."""
        m = _manifest(run_id)
        de = S.DESource.from_manifest(m)
        for reason in (de.logfc_reason, de.padj_reason):
            assert f"{m['contrast']['accession']}-analytics.tsv" in reason
            assert "Not a table you submitted" in reason
            assert "input.columns" not in reason
            assert "contrast.analytics_url" in reason

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_citation_strip_names_atlas_as_the_source(self, run_id) -> None:
        rows = {r["key"]: r for r in view_mod.table_payload(run_id)["citations"]}
        for ring in ("logfc", "neg_log10_padj"):
            row = rows[ring]
            assert row["source_name"] == "Expression Atlas", row
            assert "Not a table you submitted" in row["reason"]

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_table_phrase_names_the_contrast_not_your_table(self, run_id
                                                                 ) -> None:
        de = S.DESource.from_manifest(_manifest(run_id))
        assert "analytics.tsv" in de.table_phrase
        assert "your" not in de.table_phrase.lower()


class TestTheLegacyLabelIsADecisionNotABug:
    """One surface still prints "(yours)" on these runs, by documented intent.

    `citation_rows` prints "the run's own label, as the renderer wrote it", and
    `column_label`'s docstring gives the argument: heading an old run's column
    with today's name would put a disagreement INSIDE the table. On a run drawn
    before `c24c8ec` that rule and `sources.py`'s "the legend, the caption and
    the manifest must not be able to disagree about where a number came from"
    point opposite ways, and the row ends up saying "logFC (yours)" beside
    "Expression Atlas".

    Pinned, not asserted correct. Changing it is Puck's call; this test exists
    so that the change is deliberate rather than incidental.
    """

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_the_row_label_is_the_artefacts_and_contradicts_its_own_source(
            self, run_id) -> None:
        rows = {r["key"]: r for r in view_mod.table_payload(run_id)["citations"]}
        row = rows["logfc"]
        artefact_label = row["label"]
        if "(yours)" not in artefact_label:
            pytest.skip(f"{run_id} was drawn after the fix")
        # The contradiction, on one row, stated rather than discovered later.
        assert row["source_name"] == "Expression Atlas"
        assert "Not a table you submitted" in row["reason"]

    def test_column_label_prefers_the_artefact_when_the_build_disagrees(self
                                                                        ) -> None:
        """The mechanism, on synthetic input, so the rule is pinned independently
        of what happens to be on disk."""
        de = S.DESource.from_contrast("E-GEOD-1", "g2_g1")
        spec = {s.key: s for s in ring_specs_for(de)}["logfc"]
        # Same label as this build draws -> the written short name is used.
        assert citations_mod.column_label(spec, spec.label) == spec.column_label
        # A legacy label this build no longer draws -> printed verbatim.
        assert citations_mod.column_label(spec, "logFC (yours)") == "logFC (yours)"


class TestThePastePathIsUnchanged:
    """The qualifier has to be able to say "yours" or it says nothing when it
    says "Atlas"."""

    def test_a_manifest_with_no_contrast_block_is_the_paste_default(self) -> None:
        for m in ({}, {"contrast": None}, {"contrast": {}},
                  {"contrast": {"accession": "E-GEOD-1"}},   # no contrast_id
                  {"contrast": {"contrast_id": "g2_g1"}}):   # no accession
            de = S.DESource.from_manifest(m)
            assert de.kind == "pasted", m
            assert de.short_source == "yours"

    def test_the_paste_path_still_claims_your_own_measurement(self) -> None:
        de = S.DESource.pasted()
        assert "your own measurement" in de.logfc_reason
        assert "you submitted" in de.logfc_reason
        specs = {s.key: s for s in ring_specs_for(de)}
        assert "(yours)" in specs["logfc"].label

    def test_the_module_constant_is_still_the_paste_default(self) -> None:
        """`UNLINKED_RINGS` stays, for the reason the commit gives:
        `assert_registry_is_coherent` and callers with no run in hand need a key
        set, and it is what a run with no contrast genuinely says."""
        assert S.UNLINKED_RINGS == S.DESource.pasted().unlinked_rings()
        assert "you submitted" in S.UNLINKED_RINGS["logfc"]

    def test_unlinked_rings_for_none_is_the_paste_default(self) -> None:
        assert S.unlinked_rings_for(None) == S.UNLINKED_RINGS
        atlas = S.unlinked_rings_for(S.DESource.from_contrast("E-GEOD-1", "g1_g2"))
        assert atlas != S.UNLINKED_RINGS
        # Matched on the affirmative CLAIM, not on the words: the Atlas text
        # contains the substring "you submitted" precisely because it denies it
        # ("Not a table you submitted"), and a naive substring check here would
        # read that denial as the claim.
        assert "the DE table you submitted" not in atlas["logfc"]
        assert "Not a table you submitted" in atlas["logfc"]
        assert "the DE table you submitted" in S.UNLINKED_RINGS["logfc"]


class TestFromManifestRoundTrips:
    """A run read back off disk must reach the same answer as the run that
    wrote it — the whole reason `from_manifest` exists."""

    def test_a_contrast_source_survives_a_manifest_round_trip(self) -> None:
        built = S.DESource.from_contrast("E-GEOD-101794", "g2_g1", "110", "DESeq2")
        m = {"contrast": {"accession": "E-GEOD-101794", "contrast_id": "g2_g1",
                          "ensembl_release": "110", "pipeline": "DESeq2"}}
        assert S.DESource.from_manifest(m) == built

    def test_the_optional_fields_are_optional_and_not_invented(self) -> None:
        bare = S.DESource.from_manifest(
            {"contrast": {"accession": "E-GEOD-1", "contrast_id": "g1_g2"}})
        assert bare.kind == "contrast"
        assert "Ensembl" not in bare.logfc_reason
        assert bare == S.DESource.from_contrast("E-GEOD-1", "g1_g2")

    @pytest.mark.parametrize("run_id", CONTRAST_RUNS)
    def test_disk_and_memory_agree_for_every_contrast_run(self, run_id) -> None:
        c = _manifest(run_id)["contrast"]
        assert S.DESource.from_manifest(_manifest(run_id)) == \
            S.DESource.from_contrast(
                c["accession"], c["contrast_id"],
                str(c.get("ensembl_release") or ""), str(c.get("pipeline") or ""))


class TestAccountingUpAndDown:
    """`n_up`/`n_down` — computed, serialised, and (before `37b8d20`) rendered by
    nothing. They are what lets the heatmap state how much a signed average
    cancelled, so they have to be the counts of the SELECTED rows."""

    @staticmethod
    def rows(*pairs: tuple[float, float]) -> list[AnalyticsRow]:
        """`(pvalue, log2fc)` pairs, p given explicitly rather than derived from
        position: selection is BY p, so a helper that ties p to index hides
        which rows the assertion is actually about."""
        return [AnalyticsRow(f"ENSG{i}", f"G{i}", p, fc)
                for i, (p, fc) in enumerate(pairs)]

    def test_they_count_the_selected_rows_by_sign(self) -> None:
        top, acc = top_n_by_padj(self.rows(
            (1e-9, 2.0), (1e-8, -1.5), (1e-7, 3.0), (1e-6, -0.5)), 4)
        assert acc.n_up == 2
        assert acc.n_down == 2
        assert acc.n_up + acc.n_down == len(top)

    def test_they_count_the_TOP_rows_and_not_the_whole_table(self) -> None:
        """The distinction that makes them usable: the cancellation sentence is
        about the genes on the figure, not about the contrast. The two most
        significant rows here are both up; the three the selection leaves behind
        are all down, and must not reach these counts."""
        top, acc = top_n_by_padj(self.rows(
            (1e-9, 1.0), (1e-8, 2.0), (1e-3, -3.0), (1e-2, -4.0), (1e-1, -5.0)), 2)
        assert [r.ensembl_id for r in top] == ["ENSG0", "ENSG1"]
        assert acc.n_up == 2 and acc.n_down == 0
        assert acc.n_top == 2

    def test_a_one_directional_set_reports_zero_on_the_other_arm(self) -> None:
        """It has to be able to say zero, or it says nothing when it says 2."""
        _, acc = top_n_by_padj(self.rows(
            (1e-9, 1.0), (1e-8, 2.0), (1e-7, 3.0)), 3)
        assert acc.n_up == 3
        assert acc.n_down == 0

    def test_a_zero_log2fc_is_neither_up_nor_down(self) -> None:
        """Counted by strict sign, so a gene at exactly 0 is in neither arm and
        the two counts need not sum to the selection."""
        top, acc = top_n_by_padj(self.rows(
            (1e-9, 1.0), (1e-8, 0.0), (1e-7, -1.0)), 3)
        assert len(top) == 3
        assert acc.n_up == 1 and acc.n_down == 1
        assert acc.n_up + acc.n_down == 2

    def test_a_row_with_no_statistic_is_never_selected_or_counted(self) -> None:
        rows = self.rows((1e-9, 1.0), (1e-8, -1.0)) + [
            AnalyticsRow("ENSGX", "GX", None, 9.0)]
        top, acc = top_n_by_padj(rows, 5)
        assert acc.n_total == 3
        assert acc.n_with_statistic == 2
        assert acc.n_without_statistic == 1
        assert "ENSGX" not in {r.ensembl_id for r in top}
        assert acc.n_up == 1 and acc.n_down == 1
