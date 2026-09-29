"""Where each ring's number is documented — one registry, read by three places.

v2.8. Puck: *"can you keep links for sources in the legend."*

The scale column already named each ring's source in words ("Open Targets",
"GTEx v8", "PubTator3", "Pharos"). A name is not a citation: it does not say
*which* dataset, and it does not let a reader check the claim. This module is
the citation, and it exists as its own module for one reason — the legend, the
caption and the manifest must not be able to disagree about where a number came
from. They all read `RING_SOURCE_DOCS`. There is no second copy to drift.

Three rules it follows, and each one is a decision that could have gone the
other way:

1. **The URL points at the documentation for the field the ring reads, not at
   the source's homepage.** `opentargets.org` tells a reader nothing they did
   not already have from the word "Open Targets". So `tractable` lands on the
   prioritisation view's *Tractability section*, which is where `hasLigand`,
   `hasPocket` and `hasSmallMoleculeBinder` are defined and scored — the three
   keys the ring actually averages.

   The corollary, and `druggability` is the case that forced it: where a ring
   reads keys documented under *two* sections, the citation is the page that
   carries both, not the anchor that carries most of them. An anchor covering
   three of four keys is the plausible wrong citation this module exists to
   refuse.

2. **Where the ring's own field has no documentation page, the link says so by
   going to the page that names the upstream dataset** rather than to a
   plausible-looking neighbour. `rodent` reads OT `mousePhenotypes`; Open
   Targets publishes no page for that field (verified 2026-09-25: zero "mouse"
   entries in `platform-docs.opentargets.org/llms.txt`), and the prioritisation
   view's "Mouse models" section documents a *different* number — a harmonic-sum
   severity score, not the three-state lead this ring draws. Citing it would be
   a plausible wrong citation, which is this repo's one rule in a new place. The
   link goes to the Target data-source table, whose Mouse phenotypes row names
   MGI.

3. **A ring whose number is Puck's own, or derived here, has no entry.** `logfc`
   and `neg_log10_padj` come from the submitted table and `completeness` is
   counted by this code. `UNLINKED_RINGS` records **where the number came from**
   — not that there is nothing to link to. v3.9: those three entries used to read
   as an absence ("there is nothing to link to"), which is the same fact told as
   a gap; a reader is owed the provenance, and the provenance exists, it just is
   not a URL. No URL is invented for any of them, because a fabricated citation
   is the exact failure the rest of this module is built to prevent.

4. **The registry covers every ring key any run on disk ever drew, not the rings
   this build happens to draw.** `druggability` was retired in v2.3 and 69 runs
   under `runs/` still draw it — 991 cells. It had no entry, so it resolved
   through a `"unstated"` default nobody ever saw. That default is gone:
   `assert_every_ring_resolves` makes an unaccounted ring a raised error at
   render time, and `as_manifest_rows` raises rather than writing a manifest row
   that says a number's provenance is unstated.

What is deliberately NOT here: gene-specific deep links. Those belong in the
click-drawer, per gene, and are already there (PubTator3 and Europe PMC queries
for the gene, PubMed by PMID, ChEMBL by molecule, ClinicalTrials.gov by NCT id).
The ring legend describes the *measure*; one gene's value is the drawer's job.

Every URL in here is checked live by `scripts/check_source_links.py`, which uses
this repo's own `config.USER_AGENT` — Pharos and `druggablegenome.net` answer
403 to curl's default agent and 200 to ours, so a checker that did not use the
real agent would report a working link as dead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from . import config


@dataclass(frozen=True)
class SourceDoc:
    """One ring's citation.

    `name` and `domain` are the two things a reader of a *PNG* has, because
    nothing in a PNG or on a projected slide is clickable. That is why `domain`
    is on the figure at all: an underlined word that dies on export is no use,
    and "Open Targets" alone does not tell anyone where to type. `url` is what
    the SVG's `<a xlink:href>` carries, so the downloaded file still works in a
    browser weeks later.

    `endpoint` and `version` are for the manifest: the doc URL says what the
    number means, the endpoint says what was called, and the version is what
    pins the two together for this run.
    """

    name: str
    domain: str
    url: str
    endpoint: str
    version: str
    reads: str
    why: str
    # v3.9. Two rings may honestly share a source name, a domain and even a page,
    # and then `name · domain` is the same string for both — which is how a
    # reader ends up unable to tell one ring's citation from another's. The
    # qualifier is what goes between them when the source alone does not identify
    # the measure: `druggability` and `tractable` are both Open Targets
    # prioritisation keys on one docs page, and a reader must not read a
    # `druggability` cell as a `tractable` cell. Empty for every ring whose
    # source name already says which measure it is, which is eight of nine.
    name_qualifier: str = ""

    @property
    def legend_text(self) -> str:
        """`Open Targets · platform-docs.opentargets.org`.

        The source name first because it is the claim; the domain second
        because it is the only part that survives into a PNG as an instruction.
        A `name_qualifier` sits between them, inside the claim, because it
        narrows *what was measured* rather than where to type.
        """
        qualified = f"{self.name} ({self.name_qualifier})" if self.name_qualifier \
            else self.name
        return f"{qualified} · {self.domain}"


OT_PRIORITISATION = "https://platform-docs.opentargets.org/web-interface/target-prioritisation"
OT_DOCS_DOMAIN = "platform-docs.opentargets.org"

RING_SOURCE_DOCS: dict[str, SourceDoc] = {
    # The three dense prioritisation keys are defined, and their -1..1 scoring
    # stated, in the prioritisation view's Tractability section. Not
    # `/target/tractability`, which documents the tractability *buckets* — a
    # different dataset that this ring does not read.
    "tractable": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url=f"{OT_PRIORITISATION}#tractability-section",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads="target.prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder",
        why="defines and scores the three keys this ring averages",
    ),
    # v3.9. The retired FOUR-key form of the ring above, and the reason it is a
    # separate entry rather than an alias for it: 69 runs under `runs/` draw it
    # (991 cells), it reads one key `tractable` does not, and pointing those cells
    # at `tractable`'s anchor would tell a reader a four-key mean was a three-key
    # mean. Confirmed against the cells themselves before it was written — every
    # `druggability` row in `runs/20260924T061611Z-e8d4a4/values.csv` carries
    # `source` = "OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder,
    # hasHighQualityChemicalProbes" and a `detail` reading "mean of n/4 keys".
    #
    # The URL is the prioritisation page WITHOUT an anchor, and that is the point:
    # three of its keys are defined under `#tractability-section` and the fourth
    # under `#chemical-probes`, so no anchor documents this ring. The page that
    # carries both sections does. See rule 1's corollary in the module docstring.
    "druggability": SourceDoc(
        name="Open Targets",
        name_qualifier="retired 4-key Druggability, not Tractability",
        domain=OT_DOCS_DOMAIN,
        url=OT_PRIORITISATION,
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads=("target.prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder, "
               "hasHighQualityChemicalProbes — the mean of FOUR keys. The successor "
               "ring `tractable` is the mean of the first THREE; this one is not it"),
        why=("this ring averaged four prioritisation keys, and the fourth — "
             "hasHighQualityChemicalProbes — is documented in this page's Chemical "
             "probes section while the other three are in its Tractability section. "
             "The page is cited rather than either anchor because either anchor "
             "alone would document a different key count from the one this ring "
             "drew. Read a cell here as a 4-key mean: it is NOT the 3-key "
             "`tractable` ring, and its denominator is 4"),
    ),
    "chemical_probe": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url=f"{OT_PRIORITISATION}#chemical-probes",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads="target.prioritisation: hasHighQualityChemicalProbes",
        why="the section for the one sparse key, which is why it is its own ring",
    ),
    "safety": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url=f"{OT_PRIORITISATION}#safety-section",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads=("target.prioritisation: hasSafetyEvent, geneEssentiality, "
               "geneticConstraint, isCancerDriverGene"),
        why=("states that an absent safety record scores 0 rather than 'safe' — "
             "the caveat this ring's label carries"),
    ),
    # The endpoint doc, anchored on the operation, not the portal front page.
    # The dataset is pinned in the URL the run actually calls
    # (`datasetId=gtex_v8`) and the ring's own label already prints v8, so the
    # legend text and the manifest say the same version.
    "regional_specificity": SourceDoc(
        name="GTEx v8",
        domain="gtexportal.org/api/v2/redoc",
        url=("https://gtexportal.org/api/v2/redoc#tag/Expression-Data-Endpoints/"
             "operation/get_median_gene_expression_api_v2_expression_medianGeneExpression_get"),
        endpoint=f"{config.GTEX_BASE}/expression/medianGeneExpression",
        version=f"api v2 · dataset {config.GTEX_DATASET} · schema v{config.SCHEMA_VERSIONS['gtex.median_expression']}",
        reads="medianGeneExpression: region median TPM / median across the panel",
        why="the operation this ring calls, with its parameters and its dataset ids",
    ),
    "clinical_phase": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url="https://platform-docs.opentargets.org/target/drugs",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads="target.drugAndClinicalCandidates — maximum stage per indication",
        why=("documents the widget this ring reads, including that the maximum "
             "stage is taken across indications — the reason the ring gates on "
             "attribution (COUNCIL V1)"),
    ),
    "clinical_activity": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url="https://platform-docs.opentargets.org/drug/clinical-report",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads="clinicalReports: trialOverallStatus, trialWhyStopped category",
        why=("the clinical report record itself — the statuses and the stop "
             "categories, which is what the three states are built from"),
    ),
    # TDL is served by Pharos and *defined* by the IDG Knowledge Management
    # Centre, whose page gives the Tclin/Tchem/Tbio/Tdark criteria. Pharos'
    # own /about page documents the portal and its data sources and does not
    # define TDL anywhere (checked 2026-09-25), so linking it would be a
    # homepage with extra steps.
    "tdl": SourceDoc(
        name="Pharos",
        domain="druggablegenome.net",
        url="https://druggablegenome.net/IDGProteinFamilies",
        endpoint=config.PHAROS_GRAPHQL,
        version=f"graphql · schema v{config.SCHEMA_VERSIONS['pharos.target']}",
        reads="target(q:{sym}).tdl",
        why=("the IDG-KMC definition of the four levels; Pharos serves the "
             "field and does not define it"),
    ),
    # See rule 2 in the module docstring. This is the most specific page Open
    # Targets publishes about this field, and what it gives is the upstream
    # dataset name (MGI).
    "rodent": SourceDoc(
        name="Open Targets",
        domain=OT_DOCS_DOMAIN,
        url="https://platform-docs.opentargets.org/target#target-annotation-data-sources",
        endpoint=config.OPENTARGETS_URL,
        version=f"api/v4 graphql · target query schema v{config.SCHEMA_VERSIONS['opentargets.target']}",
        reads="target.mousePhenotypes (upstream: MGI)",
        why=("Open Targets publishes no page for the mousePhenotypes field; this "
             "table names its upstream dataset. The prioritisation view's Mouse "
             "models section documents a different number and is NOT cited here"),
    ),
    "literature": SourceDoc(
        name="PubTator3",
        domain="ncbi.nlm.nih.gov/research/pubtator3",
        url="https://www.ncbi.nlm.nih.gov/research/pubtator3/api",
        endpoint=config.PUBTATOR_SEARCH,
        version=f"search api · schema v{config.SCHEMA_VERSIONS['pubtator.count']}",
        reads="search?text=@GENE_<id> — records matching the resolved NCBI Gene id",
        why=("the API this ring counts through, which is where the gene-id "
             "resolution that stops common-word symbols inflating is specified"),
    ),
}

# Stated, not omitted. A reader who sees links on ten rings and none on three is
# entitled to know whether the other three were forgotten.
#
# v3.9: these say where the number CAME FROM, in the positive. They used to read
# "there is nothing to link to", which answers a question about URLs and leaves
# the reader's actual question — where did this number come from? — unanswered.
# Every one of the three has a definite provenance; none of them has a URL, and
# inventing one would be fabricating a citation.
UNLINKED_RINGS: dict[str, str] = {
    "logfc": ("the logFC column of the DE table you submitted — your own "
              "measurement, read straight off your file and not transformed. The "
              "run's manifest records which header it was read from "
              "(`input.columns.logfc`) and `values.csv` carries the value per gene"),
    "neg_log10_padj": ("the adjusted-p column of the DE table you submitted, drawn "
                       "as −log10(padj) so that more significant is larger — your "
                       "own measurement, with that one transform and nothing else. "
                       "The run's manifest records which header it was read from "
                       "(`input.columns.padj`) and the cell detail prints the "
                       "original p back"),
    "completeness": ("computed here, by counting rings 3-11: k is how many of the "
                     "nine external rings have a real measurement for this gene. It "
                     "is arithmetic over the other rings' cells, so its provenance "
                     "is theirs — no source of its own is read for it"),
}


def unresolved_rings(ring_keys: Iterable[str]) -> list[str]:
    """The ring keys that have neither a citation nor a stated reason.

    One predicate, so the render-time assertion, the manifest writer and the test
    that walks `runs/*/values.csv` all agree on what "accounted for" means.
    """
    return sorted({k for k in ring_keys
                   if k not in RING_SOURCE_DOCS and k not in UNLINKED_RINGS})


def assert_every_ring_resolves(ring_keys: Iterable[str]) -> None:
    """v3.9 item 3. A ring nobody accounted for stops the render.

    This is the check that was missing. `as_manifest_rows` used to fall through to
    the literal string `"unstated"` for an unknown key, and because nothing read
    that string, `druggability` sat in 991 manifest rows describing its own
    provenance as unstated for four days without anybody noticing. A default that
    produces a plausible-looking row for an unaccounted number is the same defect
    as imputing a missing value: the artefact looks complete and is not.

    Deliberately an exception and not a warning. The alternative is a figure whose
    manifest cannot say where one of its rings came from, and `AGENTS.md`'s first
    rule prices that above a crash.
    """
    missing = unresolved_rings(ring_keys)
    if missing:
        raise AssertionError(
            f"ring(s) {missing} have neither an entry in RING_SOURCE_DOCS nor a "
            f"stated reason in UNLINKED_RINGS. Every ring a run draws must resolve "
            f"to a citation or to a reason there is none — add one, and do not "
            f"borrow a neighbouring ring's URL to do it.")


def as_manifest_rows(ring_keys: list[str]) -> list[dict[str, str]]:
    """The manifest's copy of the legend's citations, in ring order.

    Built from the same dict the figure draws from, which is the whole point:
    a figure and its manifest cannot disagree about where a number came from,
    because there is one registry and neither of them owns it.

    Raises rather than defaulting. There is no row shape for "we do not know", so
    there is no row: see `assert_every_ring_resolves`.
    """
    assert_every_ring_resolves(ring_keys)
    rows = []
    for key in ring_keys:
        doc = RING_SOURCE_DOCS.get(key)
        if doc is None:
            rows.append({"ring": key, "linked": "no",
                         "reason": UNLINKED_RINGS[key],
                         "legend_text": "", "doc_url": "", "endpoint": "",
                         "version": "", "reads": ""})
            continue
        rows.append({"ring": key, "linked": "yes", "reason": doc.why,
                     "legend_text": doc.legend_text, "doc_url": doc.url,
                     "endpoint": doc.endpoint, "version": doc.version,
                     "reads": doc.reads})
    return rows


def assert_registry_is_coherent(ring_keys: Iterable[str] = ()) -> None:
    """Cheap structural checks, run at import of the figure module.

    None of these catch a *wrong* citation — only a checker hitting the network
    does that, and `scripts/check_source_links.py` is it. What they catch is the
    class of edit that silently turns a citation back into a homepage: an http
    URL, a domain printed on the figure that is not the domain the link goes to,
    or a bare origin with no path.

    `ring_keys`, where given, are the rings about to be drawn: every one of them
    must resolve (v3.9 item 3). Default-empty so a caller that only wants the
    shape checks still gets them, and so the coverage question is asked by
    whoever knows which rings are in play.
    """
    assert_every_ring_resolves(ring_keys)
    for key, doc in RING_SOURCE_DOCS.items():
        if not doc.url.startswith("https://"):
            raise AssertionError(f"{key}: source doc URL is not https: {doc.url}")
        host = doc.url.split("/", 3)[2]
        shown = doc.domain.split("/", 1)[0]
        # A leading `www.` is dropped from what the figure prints and only from
        # that: `ncbi.nlm.nih.gov/research/pubtator3/api` serves the same page
        # as the `www.` host (checked live 2026-09-25), and four characters of
        # noise per line in a column this tight is not worth nothing. Any other
        # difference is a reader typing the wrong address.
        if host.removeprefix("www.") != shown.removeprefix("www."):
            raise AssertionError(
                f"{key}: the figure prints {shown!r} and the link goes to "
                f"{host!r}; a reader of the PNG would type the wrong address")
        path = doc.url.split("/", 3)[3] if doc.url.count("/") > 2 else ""
        if not path.strip("/"):
            raise AssertionError(
                f"{key}: {doc.url} is a homepage, not documentation for a field")
        if not (doc.endpoint and doc.version and doc.reads):
            raise AssertionError(f"{key}: manifest fields are incomplete")
    overlap = set(RING_SOURCE_DOCS) & set(UNLINKED_RINGS)
    if overlap:
        raise AssertionError(f"rings both linked and declared unlinked: {overlap}")
    # v3.9. Two rings may legitimately share a source, a domain and a docs page —
    # six of these entries are Open Targets prioritisation keys. What they may not
    # share is the sentence that says which FIELD was read, because that sentence
    # is the only thing telling a reader which measure a cell is. `druggability`
    # and `tractable` are the pair this exists for: four keys and three, one page.
    by_reads: dict[str, str] = {}
    for key, doc in RING_SOURCE_DOCS.items():
        clash = by_reads.setdefault(doc.reads, key)
        if clash != key:
            raise AssertionError(
                f"{key} and {clash} both claim to read {doc.reads!r}. Two rings "
                f"with one field description are two rings a reader cannot tell "
                f"apart; if they really read the same field they are one ring.")
    # And where the URL is identical, the printed claim must not be, or the strip
    # shows one row twice over with two different numbers behind it.
    by_url: dict[str, list[str]] = {}
    for key, doc in RING_SOURCE_DOCS.items():
        by_url.setdefault(doc.url, []).append(key)
    for url, keys in by_url.items():
        texts = {RING_SOURCE_DOCS[k].legend_text for k in keys}
        if len(keys) > 1 and len(texts) != len(keys):
            raise AssertionError(
                f"rings {sorted(keys)} share {url} and print the same "
                f"{texts!r}; set `name_qualifier` so a reader can tell which "
                f"measure a cell is")


__all__ = ["SourceDoc", "RING_SOURCE_DOCS", "UNLINKED_RINGS",
           "as_manifest_rows", "assert_registry_is_coherent",
           "unresolved_rings", "assert_every_ring_resolves"]
