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
   counted by this code. `UNLINKED_RINGS` records why, so "no link" is a stated
   decision in the manifest rather than an omission a reader has to notice.

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

    @property
    def legend_text(self) -> str:
        """`Open Targets · platform-docs.opentargets.org`.

        The source name first because it is the claim; the domain second
        because it is the only part that survives into a PNG as an instruction.
        """
        return f"{self.name} · {self.domain}"


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

# Stated, not omitted. A reader who sees links on nine rings and none on three
# is entitled to know whether the other three were forgotten.
UNLINKED_RINGS: dict[str, str] = {
    "logfc": "your own submitted DE table — there is nothing to link to",
    "neg_log10_padj": "your own submitted DE table — there is nothing to link to",
    "completeness": ("derived here by counting rings 3-11; it is a gauge over the "
                     "other rings' sources, not a source"),
}


def as_manifest_rows(ring_keys: list[str]) -> list[dict[str, str]]:
    """The manifest's copy of the legend's citations, in ring order.

    Built from the same dict the figure draws from, which is the whole point:
    a figure and its manifest cannot disagree about where a number came from,
    because there is one registry and neither of them owns it.
    """
    rows = []
    for key in ring_keys:
        doc = RING_SOURCE_DOCS.get(key)
        if doc is None:
            rows.append({"ring": key, "linked": "no",
                         "reason": UNLINKED_RINGS.get(key, "unstated"),
                         "legend_text": "", "doc_url": "", "endpoint": "",
                         "version": "", "reads": ""})
            continue
        rows.append({"ring": key, "linked": "yes", "reason": doc.why,
                     "legend_text": doc.legend_text, "doc_url": doc.url,
                     "endpoint": doc.endpoint, "version": doc.version,
                     "reads": doc.reads})
    return rows


def assert_registry_is_coherent() -> None:
    """Cheap structural checks, run at import of the figure module.

    None of these catch a *wrong* citation — only a checker hitting the network
    does that, and `scripts/check_source_links.py` is it. What they catch is the
    class of edit that silently turns a citation back into a homepage: an http
    URL, a domain printed on the figure that is not the domain the link goes to,
    or a bare origin with no path.
    """
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


__all__ = ["SourceDoc", "RING_SOURCE_DOCS", "UNLINKED_RINGS",
           "as_manifest_rows", "assert_registry_is_coherent"]
