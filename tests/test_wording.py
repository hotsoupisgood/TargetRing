"""No internal build code in reader-visible text, and no sentence that
contradicts the one beside it.

Review of 2026-10-20, item 3. Nine surfaces printed this build's own review
codes at a reader -- "(COUNCIL V13: your own logFC and padj are not counted)" on
every gene's completeness hover, "(AGENTS.md rule 7)" on the flow page -- and
four pairs of sentences disagreed with each other.

The checks that matter most are in `scripts/ui_checks.py`, which reads the
rendered page. These are the ones that can run without a browser:

1. `strip_internal_codes` is a NO-OP on every string the current source writes.
   That is the test that stops the codes coming back one adapter at a time: the
   source was edited, not wrapped, so a new code in a new cell detail fails
   here.
2. The strip keeps the fact and takes only the reference.
3. One name for criterion 3, and it is Puck's.
4. The moving-range count is counted, and the Python and JS halves of the
   sentence agree.

No network, no model call.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from targetring import config, evidence, invariants, manifest, recommend, sources
from targetring.figure import RING_SPECS, moving_range_count
from targetring.wording import has_internal_code, strip_internal_codes

STATIC = config.REPO_ROOT / "targetring" / "static"
DEMO = config.REPO_ROOT / "docs" / "demo" / "api" / "run" / "20261018T190526Z-94ef5f"


class TestTheStripKeepsTheFact:
    @pytest.mark.parametrize("before,after", [
        ("3 of 9 external rings have a real measurement "
         "(COUNCIL V13: your own logFC and padj are not counted)",
         "3 of 9 external rings have a real measurement "
         "(your own logFC and padj are not counted)"),
        ("Refusing a shorter mean — the denominator would encode the answer "
         "(COUNCIL V4).",
         "Refusing a shorter mean — the denominator would encode the answer."),
        ("the count is in the drawer and is deliberately NOT on the ring "
         "— COUNCIL V9)",
         "the count is in the drawer and is deliberately NOT on the ring)"),
        ("is drawer evidence only and drives no ring (QA B6, COUNCIL D2).",
         "is drawer evidence only and drives no ring."),
        ("no ranking of any kind (AGENTS.md rule 7). The spoke order is a "
         "decision",
         "no ranking of any kind. The spoke order is a decision"),
    ])
    def test_the_sentence_survives_the_code(self, before: str, after: str) -> None:
        assert strip_internal_codes(before) == after

    def test_it_is_idempotent(self) -> None:
        once = strip_internal_codes("a measurement (COUNCIL V13: counted)")
        assert strip_internal_codes(once) == once

    def test_clean_text_is_returned_unchanged(self) -> None:
        """By identity, so the function cannot reach a string it has no
        business in. The first draft collapsed the space in "'region ... was
        inferred'" in a manifest string that had no code in it."""
        s = "run_conditions: 'region ... was inferred'  two  spaces."
        assert strip_internal_codes(s) is s

    def test_the_runs_own_numbers_survive(self) -> None:
        """A code is partly a number ("COUNCIL V13"), so this cannot be "the
        digits are unchanged" — that assertion belongs to the migrations in
        `scripts/restamp_wording.py` and is made there. What must hold here is
        that no measurement is touched."""
        for before, kept in (
                ("7 of 9 external rings (COUNCIL V13: not counted)", "7 of 9"),
                ("adjusted p 2.19538e-22 (COUNCIL V4)", "2.19538e-22"),
                ("40606 PubTator3 records — COUNCIL M6", "40606")):
            after = strip_internal_codes(before)
            assert kept in after, (before, after)
            assert not has_internal_code(after)


class TestNoSourceStringCarriesACode:
    """`strip_internal_codes` is a no-op on what the build writes today.

    Each case is a registry of reader-visible strings. A code reaching a reader
    through one of these is how all nine of them got there.
    """

    def _assert_clean(self, label: str, texts) -> None:
        bad = [t for t in texts if t and has_internal_code(t)]
        assert not bad, f"{label}: {bad[:2]}"

    def test_ring_specs(self) -> None:
        for spec in RING_SPECS:
            self._assert_clean(
                f"ring {spec.key}",
                [spec.label, spec.source, spec.units, spec.transform,
                 spec.short_label, spec.table_label, spec.printed_units,
                 spec.bound_units,
                 *(spec.category_labels or {}).values()])

    def test_source_docs(self) -> None:
        for key, doc in sources.RING_SOURCE_DOCS.items():
            self._assert_clean(f"source {key}",
                               [doc.why, doc.reads, doc.legend_text, doc.version])

    def test_criteria_and_verdict_text(self) -> None:
        block = recommend.criteria_block()
        self._assert_clean("criteria", json.dumps(block, default=str).split("\n"))

    def test_evidence_rule_text(self) -> None:
        for rule in evidence.RULES:
            self._assert_clean(f"rule {rule.key}", [rule.why])

    def test_invariant_constants(self) -> None:
        self._assert_clean("I5 affects", [invariants.EPMC_DRIVES_NO_RING])

    def test_manifest_notes(self) -> None:
        text = json.dumps({k: v for k, v in vars(manifest).items()
                           if isinstance(v, (str, list, dict, tuple))},
                          default=str)
        assert not has_internal_code(text), re.findall(
            r".{60}(?:COUNCIL|AGENTS\.md).{30}", text)[:2]

    def test_the_js_only_names_a_code_in_a_comment(self) -> None:
        """app.js is the other half of the page's text, and it is not Python.

        A code is allowed in a `//` comment — that reader is a reader of the
        code. It is not allowed in a line that renders.
        """
        bad = []
        for i, line in enumerate(
                (STATIC / "app.js").read_text().splitlines(), start=1):
            if not re.search(r"\b(?:COUNCIL\b|AGENTS\.md)", line):
                continue
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            bad.append(f"app.js:{i}: {line.strip()[:70]}")
        assert not bad, bad

    def test_the_flow_page_and_its_payload(self) -> None:
        for name in ("docs/flow/index.html", "docs/flow/flow.json"):
            text = (config.REPO_ROOT / name).read_text()
            hits = [m for m in re.findall(r".{50}(?:COUNCIL\b|AGENTS\.md).{25}", text)
                    if "//" not in m[:52] and "<!--" not in m[:52]]
            assert not hits, (name, hits[:2])


class TestThePublishedRunIsClean:
    """The artefacts the page serves. `scripts/restamp_wording.py` is what
    rewrote them, and this is the assertion that it ran."""

    @pytest.mark.skipif(not DEMO.is_dir(), reason="no exported demo run")
    @pytest.mark.parametrize("name", ["values.json", "values.csv", "evidence.json",
                                      "manifest.json", "scales.json"])
    def test_no_code_in_the_artefact(self, name: str) -> None:
        text = (DEMO / name).read_text()
        hits = re.findall(r".{60}(?:COUNCIL\b|AGENTS\.md).{30}", text)
        assert not hits, hits[:2]

    @pytest.mark.skipif(not DEMO.is_dir(), reason="no exported demo run")
    def test_i7_does_not_say_a_ranking_was_survived(self) -> None:
        """The tagline says nothing is ranked. I7's PASS line said a headline
        marker "survives the ranking", which reads as though one existed."""
        m = json.loads((DEMO / "manifest.json").read_text())
        i7 = next(i for i in m["invariants"] if i["id"] == "I7")
        assert "survives the ranking" not in i7["detail"]
        assert "no headline marker is drawn on the figure" in i7["detail"]

    @pytest.mark.skipif(not DEMO.is_dir(), reason="no exported demo run")
    def test_i5_says_which_europe_pmc_call_went_live(self) -> None:
        """I5 said "checked live"; every drawer said "replayed from cache".
        Both were true of different calls and neither said so."""
        m = json.loads((DEMO / "manifest.json").read_text())
        i5 = next(i for i in m["invariants"] if i["id"] == "I5")
        assert "integrity probe" in i5["detail"]
        assert "read from this machine's cache" in i5["detail"]

    @pytest.mark.skipif(not DEMO.is_dir(), reason="no exported demo run")
    def test_the_run_records_nine_invariants(self) -> None:
        """The README said seven. Seven is the paste path; a contrast run adds
        I8 and I9, and the page's run is a contrast run."""
        m = json.loads((DEMO / "manifest.json").read_text())
        assert [i["id"] for i in m["invariants"]] == [f"I{n}" for n in range(1, 10)]

    def test_the_readme_states_the_count_the_page_runs(self) -> None:
        text = (config.REPO_ROOT / "README.md").read_text()
        assert "Seven invariants run on every" not in text
        assert "Nine invariants run on every" in text


class TestOneNameForCriterionThree:
    def test_the_criteria_block_uses_pucks_words(self) -> None:
        """Puck, 2026-10-18: "we need the gene to be expressed, and also
        misregulated in the study, and have a strong phenotype in a study".
        The spec block said "Phenotype in a model" while the four verdict
        sentences, the 11 baked drawers and the README said "a study"."""
        spec = {c["key"]: c for c in recommend.criteria_spec()}
        assert spec["phenotype"]["name"] == "Phenotype in a study"

    def test_no_surface_says_model(self) -> None:
        text = json.dumps(recommend.criteria_block(), default=str)
        assert "henotype in a model" not in text


class TestTheMovingRangeCountIsCounted:
    def test_four_of_the_twelve_move(self) -> None:
        """The sentence said nine while the strip under it showed four. The
        condition is `_norm_for`'s own: a categorical ring has no range and a
        ring with a declared domain has a fixed one."""
        moving = [r.key for r in RING_SPECS if r.range_moves]
        assert moving == ["logfc", "neg_log10_padj", "regional_specificity",
                          "literature"]
        assert moving_range_count(RING_SPECS) == 4

    def test_a_fixed_domain_does_not_move(self) -> None:
        for spec in RING_SPECS:
            if spec.domain is not None or spec.categorical:
                assert not spec.range_moves, spec.key

    def test_nothing_hard_codes_the_old_number(self) -> None:
        for name in ("targetring/static/app.js", "targetring/figure.py"):
            text = (config.REPO_ROOT / name).read_text()
            rendered = [ln for ln in text.splitlines()
                        if "Nine of the twelve" in ln
                        and not ln.lstrip().startswith(("#", "//", "*"))]
            assert not rendered, (name, rendered[:2])


class TestOneNameOneFormat:
    """Review of 2026-10-20, item 5. `modernise` is the rule and the SOURCE
    strings were edited to its output, so it is a no-op on what the build
    writes today — which is the assertion that stops them drifting back."""

    def test_the_region_has_one_display_form(self) -> None:
        from targetring.wording import region_label
        assert region_label("Small_Intestine_Terminal_Ileum") == (
            "Small Intestine Terminal Ileum")
        # And it does not rename the organ. `REGION_SHORT` mapped both of these
        # to "ileum", and GTEx holds them as different tissue samples.
        assert region_label("Terminal_Ileum") != region_label(
            "Small_Intestine_Terminal_Ileum")

    def test_the_js_half_agrees(self) -> None:
        """`regionLabel` in `app.js`, read rather than run: it is a one-line
        arrow function and the pattern is the whole of it."""
        js = (STATIC / "app.js").read_text()
        m = re.search(r"^const regionLabel = .*$", js, re.M)
        assert m, "app.js no longer declares a top-level `regionLabel`"
        assert "replace(/_/g" in m.group(0), m.group(0)
        assert "toLowerCase" not in m.group(0), (
            "GTEx's own capitalisation is kept — see `wording.region_label`")

    def test_a_count_is_grouped_and_an_identifier_is_not(self) -> None:
        from targetring.wording import modernise
        assert modernise("65217 genes") == "65,217 genes"
        for untouched in ("ENSG00000081041", "E-GEOD-101794", "b39c02a638ef",
                          "2026-10-18", "#808080", "2200-step colormap",
                          "20261018T190526Z-94ef5f",
                          # A BARE PMID. Every exemption above works off a
                          # neighbouring letter or separator, and a PMID has
                          # neither: this one is exempt because the whole
                          # string is digits, so it is an identifier and not a
                          # sentence with a count in it. Grouping it to
                          # "11,911,824" broke all 12 PubMed links in the tool
                          # and shipped (link check, 2026-10-21).
                          "11911824", "9892619", "20643340"):
            assert modernise(untouched) == untouched, untouched

    def test_an_already_grouped_identifier_is_left_alone(self) -> None:
        """`modernise` must be a no-op on text a previous pass corrupted, so
        the repair can be the restamp's and only the restamp's."""
        from targetring.wording import modernise
        assert modernise("11,911,824") == "11,911,824"

    def test_a_year_is_not_a_count(self) -> None:
        from targetring.wording import modernise
        assert modernise("measured 2026") == "measured 2026"

    def test_the_bounds_print_as_glyphs(self) -> None:
        from targetring.wording import modernise
        assert modernise("TPM >= 1 and p <= 0.05") == "TPM ≥ 1 and p ≤ 0.05"
        # The dashboard language standard of 2026-10-21 forbids an em dash in
        # reader-visible text (rule 5), so a spaced "--" closes to a comma. It
        # used to become an em dash, which is the thing that rule bans.
        assert modernise("yes (area) -- met on 2") == "yes (area), met on 2"
        assert "—" not in modernise("yes (area) -- met on 2")
        # A hyphenated word and a flag survive: the dash rule needs spaces.
        assert modernise("gene-id resolved --force") == "gene-id resolved --force"
        # Idempotent: the output holds no spaced "--" for a second pass to find.
        once = modernise("a -- b -- c")
        assert once == "a, b, c" and modernise(once) == once

    def test_a_gene_is_not_called_a_target(self) -> None:
        from targetring.wording import modernise
        assert modernise("2 candidates hit this target") == (
            "2 candidates hit this gene")
        assert modernise("ChEMBL target record exists with 0") == (
            "a ChEMBL record exists with 0")
        # Open Targets keeps its own name, and so do its own field names.
        for proper in ("Open Targets holds 3 rows",
                       "Open Targets target-disease associations",
                       "platform-docs.opentargets.org/target-prioritisation"):
            assert modernise(proper) == proper, proper

    def test_the_rule_version_does_not_move_with_the_typography(self) -> None:
        """`recommend.rule_version` hashes the rule and its thresholds. v4.3
        printed the bounds as glyphs, and a hash that moved with them would
        tell every reader of every baked verdict that the RULE had changed."""
        t = recommend.current_thresholds()
        assert "≥" in t.sentence() and ">=" not in t.sentence()
        assert ">=" in recommend._canonical(t.sentence())
        assert recommend.rule_version(t) == "bdc70c8fc230"

    def test_the_rule_version_does_not_move_with_the_units_wording(self) -> None:
        """The same guard, for the units sweep of 2026-10-21 10:05.

        That sweep spelled "logFC" out as "log2 fold change" and gave the mouse
        ring its 0-to-1 scale, in `Thresholds.sentence` among other places. The
        bound and the quantity are the same; only the words moved. So the hash
        must not move, or all 46 baked verdicts would claim a rule change that
        did not happen. `RULE_WORDS` is what folds the wording back.
        """
        t = recommend.current_thresholds()
        assert "log2 fold change" in t.sentence()
        assert "mouse phenotype ring above 0 on its 0-to-1 scale" in t.sentence()
        canonical = recommend._canonical(t.sentence())
        assert "|logFC|" in canonical
        assert "mouse phenotype ring > 0 or" in canonical
        assert "log2 fold change" not in canonical
        assert recommend.rule_version(t) == "bdc70c8fc230"

    def test_the_rule_version_still_moves_when_a_threshold_moves(self) -> None:
        """The other half: the fold-back must not deafen the hash to a real
        change. A different bound on the same quantity is a different rule."""
        t = recommend.current_thresholds()
        moved = dataclasses.replace(t, min_abs_logfc=t.min_abs_logfc + 1)
        assert recommend.rule_version(moved) != recommend.rule_version(t)

    def test_modernise_is_a_no_op_on_what_the_build_writes(self) -> None:
        """Every reader-visible registry, through the rule. A new adapter
        string with `>=` or "this target" in it fails here."""
        from targetring.wording import modernise
        texts: list[str] = []
        for spec in RING_SPECS:
            texts += [spec.label, spec.source, spec.units, spec.transform,
                      spec.table_label, spec.printed_units, spec.bound_units]
        for doc in sources.RING_SOURCE_DOCS.values():
            texts += [doc.why, doc.reads, doc.legend_text]
        for rule in evidence.RULES:
            texts.append(rule.why)
        texts.append(json.dumps(recommend.criteria_block(), default=str))
        texts.append(invariants.EPMC_DRIVES_NO_RING)
        bad = [t for t in texts if t and modernise(t) != t]
        assert not bad, [(t[:70], modernise(t)[:70]) for t in bad[:2]]

    @pytest.mark.skipif(not DEMO.is_dir(), reason="no exported demo run")
    @pytest.mark.parametrize("name", ["values.csv", "values.json", "manifest.json",
                                      "evidence.json"])
    def test_the_published_artefacts_are_in_the_one_form(self, name: str) -> None:
        text = (DEMO / name).read_text()
        # The region id survives where it IS the identifier: the manifest's own
        # `region` key, and the GTEx query it was used in.
        assert "TPM in Small_Intestine_Terminal_Ileum" not in text
        assert "this target" not in text
        assert "human targets" not in text
        # A hex colour is not a count.
        assert not re.search(r"#[0-9a-f]{1,3},[0-9a-f]{3}\b", text)
