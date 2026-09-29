"""`ContrastRef` — one Expression Atlas contrast, carried as a unit.

Stage 1 puts only this dataclass here. Stage 3 adds `load_analytics`,
`top_n_by_padj` and `Accounting` beside it.

**Why this is a frozen dataclass and not a pair of strings.** The spike's first
finding was that the contrast id `g2_g1` means *reference* `g2`, *test* `g1`, so
reading the id left to right inverts every log2 fold change — and nothing on the
wire says which token is which. A figure drawn from an inverted sign is right in
every number and wrong about the direction of the disease, which is
`AGENTS.md:17`'s worst output arriving with no data error anywhere. So:

* the two group ids and the two group *labels* travel together with the id, and
* the constructor asserts `id == f"{ref}_{test}"` every single time (I8), and
* the prose `<name>` from `-configuration.xml` is carried verbatim, because a
  name built from the tokens is a name that can be built the wrong way round.

`sign_sentence` is the one sentence every artefact that uses this contrast must
print. It is a property rather than a string the caller writes, so the figure,
the picker and the manifest cannot word it differently — or, worse, the same way
with the labels swapped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

#: Platform value for a contrast with no `<array_design>` in its `<analytics>`
#: block. Not the string "RNA-seq" from the spike: the rest of the codebase says
#: `rnaseq`, and the picker greys on `platform != RNASEQ`.
RNASEQ = "rnaseq"


class ContrastIdentityError(AssertionError):
    """I8 failed: the id is not `{reference}_{test}`.

    An `AssertionError` subclass so `app.py`'s existing handler returns the
    invariant-abort shape, and a named class so the picker can tell this apart
    from any other assertion and render the row as `INVALID — sign unsafe`
    instead of dropping it. A dropped row is a contrast a reader never learns
    about; a greyed one with a reason is a fact.
    """


@dataclass(frozen=True)
class ContrastRef:
    """Everything needed to fetch, draw and caption one contrast.

    Constructed only by `adapters/atlas.py`. Written to the manifest verbatim by
    `asdict()`, so a run's artefacts carry the whole reference and not a
    reconstruction of it.

    `ensembl_release` and `pipeline` come from `-analysis-methods.tsv`, which the
    index build deliberately does NOT fetch: the index is ~740 GETs at two per
    experiment, and a third file per experiment would be a third of that budget
    again for two strings that only matter once a contrast is actually drawn.
    They are empty on an index-built ref and filled by stage 3.
    """

    accession: str
    contrast_id: str
    ref_group: str
    test_group: str
    ref_label: str
    test_label: str
    n_ref: int
    n_test: int
    #: The `<contrast><name>` prose from the XML, byte-for-byte. I8 reads this
    #: back off the rendered figure, so it may not be normalised here.
    name: str
    #: `rnaseq`, or the array design accession (e.g. `A-AFFY-44`) for microarray.
    platform: str = RNASEQ
    ensembl_release: str = ""
    pipeline: str = ""
    analytics_url: str = ""
    configuration_url: str = ""
    #: When the index that produced this ref was built. Printed wherever its
    #: counts are shown, because a count from a month-old crawl is a month-old
    #: count and a reader is owed the date rather than a fresh-looking number.
    index_built_utc: str = ""

    def __post_init__(self) -> None:
        expected = f"{self.ref_group}_{self.test_group}"
        if self.contrast_id != expected:
            raise ContrastIdentityError(
                f"I8: {self.accession} contrast id {self.contrast_id!r} is not "
                f"{expected!r} = f'{{reference}}_{{test}}'. The id is the only "
                f"thing on the wire that says which group is the reference, and "
                f"reading it the other way round inverts every log2 fold change. "
                f"No figure is drawn from a contrast whose sign cannot be "
                f"established.")
        if self.n_ref < 1 or self.n_test < 1:
            raise ContrastIdentityError(
                f"I8: {self.accession} {self.contrast_id} has n_ref={self.n_ref}, "
                f"n_test={self.n_test}; a side with no assays is not a contrast.")

    @property
    def is_rnaseq(self) -> bool:
        return self.platform == RNASEQ

    @property
    def sign_sentence(self) -> str:
        """The one sentence every artefact using this contrast prints.

        A property and not a caller-supplied string: the figure, the picker and
        the manifest must not be able to word this differently, and above all
        must not be able to word it identically with the labels swapped.
        """
        return (f"positive log2FC = up in {self.test_label} (n={self.n_test}) "
                f"vs {self.ref_label} (n={self.n_ref})")

    @property
    def min_n(self) -> int:
        return min(self.n_ref, self.n_test)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "sign_sentence": self.sign_sentence,
                "is_rnaseq": self.is_rnaseq}


__all__ = ["ContrastIdentityError", "ContrastRef", "RNASEQ"]
