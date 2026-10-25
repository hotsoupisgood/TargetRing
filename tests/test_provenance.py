"""Source provenance, closed over every run on disk — v3.9.

Puck: *"you aggregated all the data so there should be nothing with no source.
research sources for all data please. double check links and all."*

An audit found one ring genuinely unsourced — `druggability`, 991 cells across 69
runs — and the reason it stayed unsourced for four days is the shape of the defect
these tests exist to hold shut: `as_manifest_rows` had a `"unstated"` default, so
an unaccounted ring produced a **complete-looking manifest row** instead of an
error. Nothing read the string, so nothing objected.

The four groups below are the four items:

* `TestTheRetiredRingHasItsOwnCitation` — item 1. `druggability` is the retired
  FOUR-key form of `tractable`; it now has its own entry, and the print makes the
  key count explicit so a reader cannot take one for the other.
* `TestTheUnlinkableRingsStateTheirProvenance` — item 2. Three rings have no URL
  and never will. They say where their number came from, not that there is
  nothing to link to.
* `TestEveryRingKeyEverDrawnResolves` — item 3, and the point of the whole file:
  it walks the keys **actually present in `runs/*/values.csv`**. A hardcoded list
  would have passed while `druggability` was missing, which is exactly what the
  old checks did.
* `TestTheAppendedLedgerSectionIsNotReadAsCells` — item 4.

No network and no analysis (AGENTS.md rules 8, 9). `scripts/check_source_links.py`
is the only thing that fetches these URLs and it is run by hand.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path

import pytest

from targetring import citations, sources
from targetring import table as table_mod
from targetring.figure import RING_SPECS

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"

# The three rings whose number is Puck's own or is arithmetic done here. Named,
# because "which rings have no URL" is a decision and not an accident, and a test
# that derived this list from `UNLINKED_RINGS` would assert nothing about it.
NO_URL_RINGS = ("logfc", "neg_log10_padj", "completeness")


def _run_ids() -> list[str]:
    return sorted(p.name for p in RUNS.iterdir()
                  if p.is_dir() and table_mod.valid_run_id(p.name)
                  and (p / "values.csv").exists())


@pytest.fixture(scope="module")
def keys_by_run() -> dict[str, list[str]]:
    """Every run under `runs/`, and the ring keys it actually drew.

    Read through `table.ring_keys_drawn`, so this fixture cannot be the thing that
    reads a drop-ledger row as a ring (item 4). Runs that refuse for their own
    reasons are still recorded — as an empty list — because a run this audit could
    not read is a hole in the audit and not a pass.
    """
    out: dict[str, list[str]] = {}
    for run_id in _run_ids():
        try:
            out[run_id] = table_mod.ring_keys_drawn(run_id)
        except table_mod.TableUnavailable:
            out[run_id] = []
    return out


@pytest.fixture(scope="module")
def every_key_ever_drawn(keys_by_run) -> set[str]:
    return {k for ks in keys_by_run.values() for k in ks}


class TestTheRetiredRingHasItsOwnCitation:
    """Item 1. Confirmed against the cells before it was written: every
    `druggability` row in `runs/20260924T061611Z-e8d4a4/values.csv` carries
    `source` = "OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder,
    hasHighQualityChemicalProbes" and a `detail` reading "mean of n/4 keys".
    """

    KEY = "druggability"
    RUN = "20260924T061611Z-e8d4a4"

    def test_the_cells_really_are_the_four_key_form(self) -> None:
        """The premise, checked rather than taken from the brief. If the cells said
        three keys, the citation written for four would be the plausible wrong
        citation this file is about."""
        rows = [r for r in table_mod.read_cell_rows(RUNS / self.RUN / "values.csv")
                if r["ring"] == self.KEY]
        assert rows, f"{self.RUN} does not draw {self.KEY}"
        for r in rows:
            assert "hasHighQualityChemicalProbes" in r["source"], r["cell_id"]
            for key in ("hasLigand", "hasPocket", "hasSmallMoleculeBinder"):
                assert key in r["source"], r["cell_id"]
            if r["state"] == "value":
                assert re.search(r"mean of \d/4 keys", r["detail"]), r["detail"]
        # And the successor really is the three-key form, or the distinction this
        # citation is written to draw would not be a distinction.
        assert "hasHighQualityChemicalProbes" not in (
            sources.RING_SOURCE_DOCS["tractable"].reads)

    def test_it_has_an_entry_of_its_own_and_not_the_successors_link(self) -> None:
        doc = sources.RING_SOURCE_DOCS[self.KEY]
        for successor in ("tractable", "chemical_probe"):
            other = sources.RING_SOURCE_DOCS[successor]
            assert doc.url != other.url
            assert doc.reads != other.reads
            assert doc.legend_text != other.legend_text, (
                f"{self.KEY} prints the same citation as {successor}")

    def test_the_printed_citation_states_the_key_count(self) -> None:
        """The wording is the whole safeguard. Sharing a docs page with `tractable`
        is honest — that page documents both — but a reader must not close it
        thinking they read about the ring beside this one."""
        doc = sources.RING_SOURCE_DOCS[self.KEY]
        assert "4-key" in doc.legend_text
        assert "Tractability" in doc.legend_text, (
            "the printed claim does not name what this ring is NOT")
        assert "FOUR" in doc.reads and "THREE" in doc.reads
        assert "tractable" in doc.reads
        assert "4" in doc.why and "Chemical probes" in doc.why

    def test_the_url_is_the_page_that_documents_all_four_keys(self) -> None:
        """Three keys are defined under `#tractability-section` and the fourth under
        `#chemical-probes`, so no anchor documents this ring — the page carrying
        both sections does. That is also why this URL has no fragment."""
        doc = sources.RING_SOURCE_DOCS[self.KEY]
        assert "#" not in doc.url
        for successor in ("tractable", "chemical_probe"):
            anchored = sources.RING_SOURCE_DOCS[successor].url
            assert anchored.startswith(doc.url) and "#" in anchored, (
                f"{successor} is no longer an anchor on {doc.url}, so the argument "
                f"for citing the unanchored page no longer holds — recheck it")

    def test_no_url_was_invented_for_it(self) -> None:
        """The cheapest way to have closed this gap was to make one up. The URL is
        a page two other rings already cite anchors on, which is the evidence that
        it exists — and `scripts/check_source_links.py` fetches it."""
        doc = sources.RING_SOURCE_DOCS[self.KEY]
        assert doc.url == sources.OT_PRIORITISATION
        assert doc.domain == sources.OT_DOCS_DOMAIN
        assert doc.url.split("/", 3)[2] == doc.domain

    def test_the_retirement_is_stated_beside_the_citation_not_instead_of_it(
            self) -> None:
        """v3.7 left this ring unlinked and said why. v3.9 keeps the "why" — a
        reader of an old run still needs to know this build has no scale for the
        column — and adds the citation that was always available."""
        p = citations.cell_provenance("no_data", self.KEY, "RID")
        assert p["kind"] == "source_doc" and p["url"]
        assert "v2.3" in p["why"], "the retirement is no longer stated"
        assert "no longer draws" in p["why"]
        # One copy of each fact. The note lives in `citations.RETIRED_RINGS` and
        # the citation in `sources`; neither restates the other.
        note = citations.RETIRED_RINGS[self.KEY]
        assert note in p["why"]
        assert sources.RING_SOURCE_DOCS[self.KEY].why in p["why"]

    def test_a_ring_cannot_be_both_linked_and_declared_unlinked(self) -> None:
        """`sources.assert_registry_is_coherent` checks this for `UNLINKED_RINGS`.
        `RETIRED_RINGS` lives in `citations` (which imports `sources`, so the check
        cannot go the other way), and this is it: a retired ring with a citation is
        a note, never a second answer to "where did this number come from"."""
        for key in citations.RETIRED_RINGS:
            assert key not in sources.UNLINKED_RINGS, (
                f"{key} is declared unlinked AND retired; those are two reasons and "
                f"a reader would be shown both")


class TestTheUnlinkableRingsStateTheirProvenance:
    """Item 2. Same fact, told as provenance rather than as a gap."""

    def test_the_three_are_exactly_the_rings_with_no_url(self) -> None:
        assert set(sources.UNLINKED_RINGS) == set(NO_URL_RINGS)
        for key in NO_URL_RINGS:
            assert key not in sources.RING_SOURCE_DOCS

    def test_each_one_says_where_the_number_came_from(self) -> None:
        """Not "there is nothing to link to", which answers a question about URLs
        and leaves the reader's question — where did this number come from? —
        unanswered."""
        reasons = sources.UNLINKED_RINGS
        for key in NO_URL_RINGS:
            assert "nothing to link to" not in reasons[key], key
        # The quantity is spelled out since the units sweep of 2026-10-21
        # 10:05: "logFC +2.6" -> "log2 fold change +2.6". What this line is
        # about is unchanged -- the reason names the COLUMN the number was
        # read from, not a URL.
        assert "log2 fold change column" in reasons["logfc"]
        assert "you submitted" in reasons["logfc"]
        assert "adjusted-p column" in reasons["neg_log10_padj"]
        assert "−log10" in reasons["neg_log10_padj"], (
            "it does not say the ring is a transform of the submitted column")
        assert "computed here" in reasons["completeness"]
        assert "counting rings 3-11" in reasons["completeness"]

    def test_not_one_of_them_carries_a_url(self) -> None:
        """The failure this item could have introduced. A plausible-looking link on
        Puck's own measurement would tell him a third party had checked it."""
        for key in NO_URL_RINGS:
            reason = sources.UNLINKED_RINGS[key]
            assert "http" not in reason, key
            p = citations.cell_provenance("no_data", key, "RID")
            assert p["kind"] == "unlinked" and p["url"] == "" and p["text"] == ""
            assert p["why"] == reason

    def test_the_manifest_row_carries_the_provenance_and_no_link(self) -> None:
        rows = {r["ring"]: r for r in sources.as_manifest_rows(list(NO_URL_RINGS))}
        for key in NO_URL_RINGS:
            r = rows[key]
            assert r["linked"] == "no" and r["doc_url"] == ""
            assert r["reason"] == sources.UNLINKED_RINGS[key]


class TestEveryRingKeyEverDrawnResolves:
    """Item 3. Walked over `runs/`, never over a list written here.

    The old checks were over `RING_SPECS` — the rings this build draws — and
    `druggability` is not one of them, so they passed for four days with 991 cells
    resolving through a `"unstated"` default. A hardcoded list of ring keys would
    have reproduced that exactly.
    """

    def test_the_audit_actually_read_the_runs(self, keys_by_run) -> None:
        """A guard on the three tests below: if this fixture silently read nothing,
        they would all pass and assert nothing at all."""
        assert len(keys_by_run) >= 240, len(keys_by_run)
        unread = [r for r, ks in keys_by_run.items() if not ks]
        assert not unread, f"{len(unread)} run(s) could not be read: {unread[:5]}"

    def test_every_key_in_every_run_resolves(self, keys_by_run,
                                             every_key_ever_drawn) -> None:
        """The claim Puck asked for, in one assertion: nothing with no source."""
        assert not sources.unresolved_rings(every_key_ever_drawn)
        sources.assert_every_ring_resolves(every_key_ever_drawn)
        for run_id, keys in keys_by_run.items():
            assert not sources.unresolved_rings(keys), run_id

    def test_the_audit_covers_a_ring_this_build_does_not_draw(
            self, every_key_ever_drawn) -> None:
        """The test that fails if this file is ever narrowed back to `RING_SPECS`.
        `druggability` is in `runs/` and not in this build, and it is the reason
        the audit is written over the artefacts."""
        drawn_now = {s.key for s in RING_SPECS}
        assert "druggability" in every_key_ever_drawn
        assert "druggability" not in drawn_now
        assert every_key_ever_drawn - drawn_now, (
            "no run on disk draws a ring this build does not — this audit is only "
            "as good as that gap, so check it before deleting it")

    def test_the_string_unstated_appears_nowhere(self, every_key_ever_drawn) -> None:
        """Not "is not reached": does not exist. The default is gone from the code,
        so there is no branch left to reach."""
        rows = sources.as_manifest_rows(sorted(every_key_ever_drawn))
        assert "unstated" not in json.dumps(rows).lower()
        for r in rows:
            assert r["reason"], r["ring"]
        # Parsed, not grepped: the docstrings that record why the default was
        # removed necessarily name it, and a line-based check cannot tell those
        # from the literal coming back as a value.
        tree = ast.parse((REPO / "targetring" / "sources.py").read_text())
        literals = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and n.value == "unstated"]
        assert not literals, (
            f"the unstated default is back as a value on line(s) "
            f"{[n.lineno for n in literals]}")

    def test_an_unaccounted_ring_raises_instead_of_writing_a_row(self) -> None:
        """The defect, reproduced against the fix. Before v3.9 this returned a row
        that looked exactly like every other row."""
        with pytest.raises(AssertionError, match="neither an entry"):
            sources.as_manifest_rows(["tractable", "a_ring_nobody_accounted_for"])
        with pytest.raises(AssertionError, match="neither an entry"):
            sources.assert_registry_is_coherent(["a_ring_nobody_accounted_for"])
        # And the message names the ring, because the point of failing is to say
        # which number has no provenance.
        with pytest.raises(AssertionError, match="a_ring_nobody_accounted_for"):
            sources.assert_every_ring_resolves(["a_ring_nobody_accounted_for"])

    def test_the_render_asserts_it_before_it_draws_a_link(self) -> None:
        """Where the check is called from matters: at render time, over the rings
        about to be drawn, so a new ring added without a citation cannot produce a
        figure at all."""
        fig = (REPO / "targetring" / "figure.py").read_text()
        assert "assert_registry_is_coherent([s.key for s in RING_SPECS])" in fig

    def test_the_citation_strip_never_falls_back_for_a_ring_on_disk(
            self, every_key_ever_drawn) -> None:
        """The table surface's own version of the same gap. `unlinked_reason` has a
        last-resort branch that says a ring is unaccounted for; it must be
        unreachable for every ring any run drew."""
        for key in sorted(every_key_ever_drawn):
            why = citations.cell_provenance("no_data", key, "RID")["why"]
            assert why
            assert "no reason on record" not in why, key
            assert f"no entry for {key!r}" not in why, key

    def test_two_rings_cannot_share_one_field_description(self) -> None:
        """The structural guard behind item 1's wording: `druggability` and
        `tractable` share a source, a domain and a page, and what tells them apart
        is the sentence naming the field. So that sentence must be unique."""
        from dataclasses import replace

        sources.assert_registry_is_coherent()
        doc = sources.RING_SOURCE_DOCS["druggability"]
        clone = replace(doc, reads=sources.RING_SOURCE_DOCS["tractable"].reads)
        saved = dict(sources.RING_SOURCE_DOCS)
        sources.RING_SOURCE_DOCS["druggability"] = clone
        try:
            with pytest.raises(AssertionError, match="both claim to read"):
                sources.assert_registry_is_coherent()
        finally:
            sources.RING_SOURCE_DOCS.clear()
            sources.RING_SOURCE_DOCS.update(saved)

    def test_an_alias_of_the_successors_citation_is_refused(self) -> None:
        """The edit this work exists to prevent, as a test: pointing `druggability`
        at `tractable`'s entry wholesale. Same URL, same printed claim, a different
        number behind it."""
        saved = dict(sources.RING_SOURCE_DOCS)
        sources.RING_SOURCE_DOCS["druggability"] = saved["tractable"]
        try:
            with pytest.raises(AssertionError):
                sources.assert_registry_is_coherent()
        finally:
            sources.RING_SOURCE_DOCS.clear()
            sources.RING_SOURCE_DOCS.update(saved)


class TestTheAppendedLedgerSectionIsNotReadAsCells:
    """Item 4. `values.csv` carries the drop ledger after the cells, under its own
    header `input_symbol,status,resolved_symbol,ensembl_id,row,reason`.

    Measured 2026-09-28 over `runs/`: **all 248** runs with a `values.csv` carry the
    appended section, and 227 of them *also* carry a separate `ledger.csv` — the
    separate file is an addition, never a replacement. Read to end of file, an
    `input_symbol` lands in `cell_id` and a drop reason lands in `value`, which puts
    a gene back in the grid it was dropped from.
    """

    def test_the_section_is_in_every_run_on_disk(self) -> None:
        """The prevalence claim, asserted rather than quoted, because the handling
        below is only worth having if the section is really there."""
        appended = [r for r in _run_ids()
                    if "# DROP LEDGER" in (RUNS / r / "values.csv").read_text()]
        assert len(appended) == len(_run_ids()) >= 240
        also_separate = [r for r in appended if (RUNS / r / "ledger.csv").exists()]
        assert 0 < len(also_separate) < len(appended), (
            "every run now writes both, or none does; either way the claim above "
            "about the separate file being an addition needs rechecking")

    def test_the_boundary_is_asserted_not_inferred_from_row_shape(self) -> None:
        """A row that is neither a cell nor a `#` marker means the file was
        truncated or edited mid-section. Stopping there quietly would drop real
        cells and report a shorter gene set with no complaint."""
        with pytest.raises(table_mod.TableUnavailable,
                           match="neither a cell row nor"):
            table_mod.read_cell_rows(self._doctored(
                lambda lines: lines[:3] + ["nonsense,0,X,logfc,,value,1,,,\n"]))

    def test_a_cell_row_after_the_boundary_is_a_refusal(self) -> None:
        """The other half: a cell section split by a marker. Reading up to the
        first marker would return a table missing everything after it."""
        def split_it(lines: list[str]) -> list[str]:
            return lines[:3] + ["\n", "# DROP LEDGER — x\n"] + lines[3:6]
        with pytest.raises(table_mod.TableUnavailable, match="appearing AFTER"):
            table_mod.read_cell_rows(self._doctored(split_it))

    def test_the_files_own_rendered_count_is_checked(self) -> None:
        """`# input_rows=15 rendered=14 ledgered=3` is the file's own statement of
        how many genes the cell section holds. It catches stopping too early and
        reading too far in one comparison."""
        def lie(lines: list[str]) -> list[str]:
            return [re.sub(r"rendered=\d+", "rendered=999", ln) for ln in lines]
        with pytest.raises(table_mod.TableUnavailable, match="disagrees with itself"):
            table_mod.read_cell_rows(self._doctored(lie))

    def test_no_ledger_row_reaches_the_cells_in_any_run(self) -> None:
        """Over every run, not a sample: no cell row carries a ledger status in the
        column a naive read would have put one in."""
        statuses = {"ambiguous", "renamed", "no_match", "failed_resolve",
                    "possible_mouse", "filtered", "filtered_no_data",
                    "dropped_no_padj", "dropped_bad_logfc", "duplicate"}
        checked = 0
        for run_id in _run_ids():
            try:
                rows = table_mod.read_cell_rows(RUNS / run_id / "values.csv")
            except table_mod.TableUnavailable:
                continue
            for r in rows:
                assert r["cell_id"].startswith("cell|"), (run_id, r["cell_id"])
                assert r["state"] not in statuses, (run_id, r["cell_id"])
                assert r["ring"] != "reason", (run_id, r["cell_id"])
            checked += 1
        assert checked >= 240, checked

    def test_a_naive_read_really_would_have_been_wrong(self) -> None:
        """The defect is a live one, not a hypothetical: a plain `DictReader` run to
        the end of a real `values.csv` yields rows that are not cells."""
        run_id = next(r for r in _run_ids()
                      if "# DROP LEDGER" in (RUNS / r / "values.csv").read_text())
        with (RUNS / run_id / "values.csv").open(newline="") as fh:
            naive = list(csv.DictReader(fh))
        strict = table_mod.read_cell_rows(RUNS / run_id / "values.csv")
        assert len(naive) > len(strict)
        extra = naive[len(strict):]
        assert any((r.get("cell_id") or "") == "input_symbol" for r in extra), (
            "the ledger's header row is not where it was; recheck `_write_csv`")

    def test_nothing_else_reads_the_csv_to_the_end(self) -> None:
        """One reader, so the boundary cannot be handled in one place and not in
        another. `de_input` is excluded: it parses Puck's uploaded table, which has
        no sections at all."""
        offenders = []
        for path in sorted((REPO / "targetring").rglob("*.py")) + \
                sorted((REPO / "scripts").rglob("*.py")):
            if path.name in ("de_input.py", "table.py"):
                continue
            text = path.read_text()
            if "values.csv" in text and re.search(r"csv\.(DictReader|reader)\(", text):
                offenders.append(path.relative_to(REPO).as_posix())
        assert not offenders, (
            f"{offenders} read values.csv with a raw csv reader; use "
            f"`table.read_cell_rows`, which stops at the section boundary")

    @staticmethod
    def _doctored(edit) -> Path:
        """A real `values.csv`, edited in a tmp file. Doctoring a real artefact is
        the point — a hand-built fixture would prove the reader handles a file
        shaped the way this test imagines, which is not the question."""
        run_id = _run_ids()[0]
        lines = (RUNS / run_id / "values.csv").read_text().splitlines(keepends=True)
        out = Path(pytest.importorskip("tempfile").mkdtemp()) / "values.csv"
        out.write_text("".join(edit(lines)))
        return out
