#!/usr/bin/env python
"""Cut `fixtures/atlas/` from the warm cache, so every test runs offline.

    .venv/bin/python scripts/make_atlas_fixtures.py

Reads the `cache/atlas.*` entries a live `scripts/build_atlas_index.py` already
wrote and writes trimmed copies into `fixtures/atlas/`. It makes **no** request:
if an accession is not in the cache it says so and stops, rather than reaching
for the network from a script whose job is to produce offline fixtures.

Two kinds of fixture, and the distinction matters:

* **raw files**, for the parsers. `-configuration.xml` is copied whole where it
  is small and trimmed to a stated number of assay groups where it is not; the
  condensed SDRF is cut to the assays those groups name, plus a stated number of
  unrelated assays so a test can see that the parser reads the right rows.
  Trimming is recorded in a `TRIMMED.md` beside them, with the original sizes.

* **the Crohn's index slice**, for the picker. A real index, produced by the real
  build, restricted to the experiments that carry a Crohn's-tagged test group —
  so the offline picker tests assert the counts a live run produces, not counts
  invented to match them.

There is also one **hand-written** fixture, and it is hand-written on purpose:
`E-FAKE-1-configuration.xml` has a contrast whose id is not `{ref}_{test}`. Eight
real contrasts in the Atlas have that defect (`E-GEOD-32175`, `E-MTAB-11354` and
six others, measured 2026-09-29), so the case is not hypothetical; the fixture is
small and local so the test does not depend on the Atlas keeping its bugs.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import atlas_index as idx  # noqa: E402
from targetring import config  # noqa: E402
from targetring.adapters import atlas as atlas_mod  # noqa: E402
from targetring.cache import Cache  # noqa: E402

OUT = config.REPO_ROOT / "fixtures" / "atlas"

#: `accession -> how many assay groups of its configuration to keep`. `None`
#: keeps the whole file.
RAW: dict[str, int | None] = {
    # RNA-seq, 2 contrasts, the experiment the spike drew its heatmap from.
    "E-GEOD-101794": None,
    # Microarray, one array design, and a disease mix (Crohn's, UC,
    # indeterminate colitis, infectious colitis, normal) that exercises the
    # ontology-id filter properly.
    "E-GEOD-6731": None,
    # The null contrast's experiment: `g6_g4` is Crohn's NORMAL tissue against
    # control, so its test group carries Crohn's as a donor CHARACTERISTIC and
    # `normal` as the experimental FACTOR. The one fixture that proves the index
    # files contrasts by factor and not by characteristic.
    "E-GEOD-57945": None,
    # 101 of the 138 Crohn's contrasts. Trimmed hard: 146 kB of XML and 2.5 MB of
    # SDRF for an experiment whose interesting property is that its assay-group
    # LABELS say "inflammatory bowel disease" while its assays are tagged
    # EFO_0000384 -- prose and ids disagreeing on the experiment that supplies
    # most of the picker.
    "E-GEOD-100833": 4,
}

#: Extra unrelated assays kept in a trimmed SDRF, so a parser test can see that
#: the index reads the rows for the groups it asked about and not the whole file.
DECOY_ASSAYS = 3

CROHNS_IDS = ("EFO_0000384", "MONDO_0005011")

FAKE_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<!-- HAND-WRITTEN. Not from the Atlas.

     `c2`'s id is `g1_g2` while its reference is g2 and its test is g1, so
     `id == f"{{ref}}_{{test}}"` fails and the sign of every log2FC in it is
     unestablished. Eight real Atlas contrasts have exactly this defect
     (measured 2026-09-29 over 1,126 contrasts), and the picker's requirement is
     that such a row is shown as `INVALID - sign unsafe` rather than dropped:
     a dropped row is a contrast a reader never learns about.

     `c1` is well-formed, so one file covers both branches. -->
<configuration experimentType="rnaseq_mrna_differential" r_data="1">
    <analytics>
        <assay_groups>
            <assay_group id="g1" label="Crohn's disease">
                <assay>RUN_A1</assay>
                <assay>RUN_A2</assay>
                <assay>RUN_A3</assay>
                <assay>RUN_A4</assay>
            </assay_group>
            <assay_group id="g2" label="normal">
                <assay>RUN_B1</assay>
                <assay>RUN_B2</assay>
                <assay>RUN_B3</assay>
                <assay>RUN_B4</assay>
            </assay_group>
        </assay_groups>
        <contrasts>
            <contrast id="g2_g1">
                <name>'Crohn's disease' vs 'normal'</name>
                <reference_assay_group>g2</reference_assay_group>
                <test_assay_group>g1</test_assay_group>
            </contrast>
            <contrast id="g1_g2">
                <name>a contrast whose id disagrees with its groups</name>
                <reference_assay_group>g2</reference_assay_group>
                <test_assay_group>g1</test_assay_group>
            </contrast>
        </contrasts>
    </analytics>
</configuration>
"""

FAKE_SDRF = "\n".join(
    f"E-FAKE-1\t\t{assay}\t{kind}\tdisease\t{value}\t{uri}"
    for assay, value, uri in
    [(f"RUN_A{i}", "Crohn's disease", "http://www.ebi.ac.uk/efo/EFO_0000384")
     for i in range(1, 5)]
    + [(f"RUN_B{i}", "normal", "http://purl.obolibrary.org/obo/PATO_0000461")
       for i in range(1, 5)]
    for kind in ("characteristic", "factor")
) + "\n"


def trim_configuration(xml: str, keep_groups: int) -> tuple[str, list[str]]:
    """Keep the first `keep_groups` assay groups and every contrast between them.

    Returns the XML and the assays it still names, so the SDRF can be cut to
    match. `ET` is used rather than a regex because a half-closed tag is a
    fixture that fails for the wrong reason.
    """
    root = ET.fromstring(xml)
    kept: list[str] = []
    for block in root.iter("analytics"):
        groups_el = block.find("assay_groups")
        if groups_el is None:
            continue
        groups = list(groups_el)
        for g in groups[keep_groups:]:
            groups_el.remove(g)
        kept_ids = {g.get("id") for g in groups_el}
        for g in groups_el:
            kept.extend((a.text or "").strip() for a in g.iter("assay"))
        contrasts_el = block.find("contrasts")
        if contrasts_el is None:
            continue
        for c in list(contrasts_el):
            ref = (c.findtext("reference_assay_group") or "").strip()
            test = (c.findtext("test_assay_group") or "").strip()
            if ref not in kept_ids or test not in kept_ids:
                contrasts_el.remove(c)
    return ET.tostring(root, encoding="unicode"), kept


def trim_sdrf(sdrf: str, assays: list[str]) -> str:
    wanted = set(assays)
    lines = sdrf.splitlines()
    others = [ln for ln in lines if ln.split("\t")[2] not in wanted]
    decoys = {ln.split("\t")[2] for ln in others}
    decoys = set(sorted(decoys)[:DECOY_ASSAYS])
    out = [ln for ln in lines
           if ln.split("\t")[2] in wanted or ln.split("\t")[2] in decoys]
    return "\n".join(out) + "\n"


def main() -> int:
    cache = Cache()
    OUT.mkdir(parents=True, exist_ok=True)
    notes: list[str] = [
        "# fixtures/atlas — how these files were cut",
        "",
        "Written by `scripts/make_atlas_fixtures.py` from the warm `cache/atlas.*`",
        "entries of a live `scripts/build_atlas_index.py` run. No test in this repo",
        "reaches the network; live probes live in `scripts/`.",
        "",
        "| file | original | here | how |",
        "|---|---|---|---|",
    ]

    for acc, keep in RAW.items():
        xml = cache.get("atlas.configuration", acc)
        sdrf = cache.get("atlas.condensed_sdrf", acc)
        if xml is None or sdrf is None:
            print(f"{acc}: not in the cache. Run scripts/build_atlas_index.py "
                  f"--only {acc} first.", file=sys.stderr)
            return 1
        if keep is None:
            new_xml, new_sdrf, how = xml, sdrf, "copied whole"
        else:
            new_xml, assays = trim_configuration(xml, keep)
            new_sdrf = trim_sdrf(sdrf, assays)
            how = (f"first {keep} assay groups and the contrasts between them; "
                   f"SDRF cut to their {len(assays)} assays plus {DECOY_ASSAYS} "
                   f"unrelated ones")
        (OUT / f"{acc}-configuration.xml").write_text(new_xml)
        (OUT / f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}").write_text(new_sdrf)
        notes.append(f"| `{acc}-configuration.xml` | {len(xml):,} B | "
                     f"{len(new_xml):,} B | {how} |")
        notes.append(f"| `{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}` | "
                     f"{len(sdrf):,} B | {len(new_sdrf):,} B | "
                     f"{'copied whole' if keep is None else 'cut to match'} |")
        print(f"{acc}: xml {len(xml):,} -> {len(new_xml):,}, "
              f"sdrf {len(sdrf):,} -> {len(new_sdrf):,}")

    (OUT / "E-FAKE-1-configuration.xml").write_text(FAKE_CONFIG)
    (OUT / f"E-FAKE-1{config.ATLAS_CONDENSED_SDRF_SUFFIX}").write_text(FAKE_SDRF)
    notes.append("| `E-FAKE-1-*` | — | — | HAND-WRITTEN: a contrast whose id is "
                 "not `{ref}_{test}`, which eight real Atlas contrasts are |")
    print("E-FAKE-1: hand-written (invalid contrast id)")

    # --- the Crohn's slice of the real index ------------------------------
    full = idx.AtlasIndex.load()
    crohns = full.contrasts_for(CROHNS_IDS)
    accs = sorted({c.accession for c in crohns})
    slice_ = idx.AtlasIndex(
        built_utc=full.built_utc, schema_version=full.schema_version,
        n_experiments=len(accs), population=accs,
        contrasts=[c for c in full.contrasts if c.accession in accs],
        failed=[f for f in full.failed if f["accession"] in accs])
    p = slice_.save(OUT / "index" / "crohns-index.json")
    notes.append(f"| `index/crohns-index.json` | 1,126 contrasts | "
                 f"{slice_.n_contrasts} | the real index restricted to the "
                 f"{len(accs)} experiments with a Crohn's-tagged test group |")
    print(f"\nindex slice: {len(accs)} experiments, {slice_.n_contrasts} contrasts "
          f"({len(crohns)} of them Crohn's) -> {p}")

    # --- the OLS4 payloads, as a cache directory --------------------------
    #
    # Copied as a `cache.Cache` tree rather than converted into hand-shaped
    # fixtures, for one reason: a test that points the real `Cache` at this
    # directory exercises the real cache key -- source, schema version, query --
    # so a schema bump invalidates the fixtures exactly as it invalidates the live
    # cache. Hand-shaped payloads would keep answering after a bump and the test
    # would go on passing against a parse that no longer exists.
    ols_out = OUT / "ols-cache"
    copied = 0
    for source in ("ols.select", "ols.term"):
        src = config.CACHE_DIR / source
        if not src.exists():
            print(f"{source}: nothing cached. Run a live resolve first.",
                  file=sys.stderr)
            return 1
        for f in sorted(src.rglob("*.json")):
            dest = ols_out / f.relative_to(config.CACHE_DIR)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f.read_text())
            copied += 1
    notes.append(f"| `ols-cache/` | — | {copied} entries | the live OLS4 payloads "
                 f"as a `cache.Cache` tree, so tests use the real cache key |")
    print(f"ols-cache: {copied} entries -> {ols_out}")

    (OUT / "TRIMMED.md").write_text("\n".join(notes) + "\n")
    print(f"notes -> {OUT / 'TRIMMED.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
