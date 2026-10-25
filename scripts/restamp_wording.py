#!/usr/bin/env python3
"""Rewrite a finished run's WORDING, offline. No re-analysis, no number moved.

Review of 2026-10-20, item 3. The source strings that printed this build's
review codes at a reader were edited in the same commit, so a new run writes
clean text. The run on the page was baked on 2026-10-18 and its text is on
disk: every gene's completeness reason says "(COUNCIL V13: your own logFC and
padj are not counted)" in `values.csv`, in each `drawer/<GENE>.json` and in the
manifest's own invariant details.

So this applies `wording.strip_internal_codes` to those artefacts. It is the
same function the source strings were run through, which is the point: one rule,
so the restamped run and the next live run say the same thing.

**What it is allowed to change: wording.** It rewrites string VALUES only, and
only where `strip_internal_codes` changes them. It never touches a number, a
key, a float, a gene id, a URL or a file name, and it rewrites nothing in a
`raw` block -- that is an adapter's own recorded payload. There is no network
call and no model call in it, so it cannot spend and cannot re-buy a baked
summary.

Idempotent: a second run reports zero changes. `--check` writes nothing.

    .venv/bin/python scripts/restamp_wording.py            # config.DEMO_RUNS
    .venv/bin/python scripts/restamp_wording.py <run_id> ...
    .venv/bin/python scripts/restamp_wording.py --check
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import config, table as table_mod  # noqa: E402
from targetring.wording import modernise  # noqa: E402

#: Keys whose value is an adapter's or a renderer's own recorded payload rather
#: than a sentence for a reader. Never rewritten, at any depth.
OPAQUE_KEYS = frozenset({"raw", "git_sha", "run_id", "gene", "key", "url",
                         "query", "pubtator_query", "europepmc_query",
                         # A filter expression is the SYNTAX the analyse form
                         # parses, echoed back at the reader. Respelling
                         # "literature >= 2" as "literature ≥ 2" would
                         # document an input the parser rejects.
                         "expression", "filters", "region"})

#: Two of those keys are opaque because of what a STRING under them is: a
#: filter expression ("literature >= 2") is the syntax the analyse form parses,
#: echoed back at the reader, and a region key is the underscored GTEx tissue
#: id. Neither is ever a dict. A drawer's `summary.inputs.expression` IS a
#: dict -- the gene's expression facts, with a reader's sentence under `note`
#: -- and skipping it by name left "a ratio against the panel median reads as
#: tissue-specific" baked in the four runs after the wording pass of
#: 2026-10-20 had already fixed the source string. Same class of defect as the
#: PMID grouping below: a key name matched where the VALUE was a different
#: kind of thing. So these two are opaque only where they are scalars.
BY_SCALAR_ONLY = frozenset({"expression", "region"})

#: Keys whose value is a BARE-DIGIT identifier, at any depth. A wording pass
#: must not reach one, and this pass already did: `modernise` read each PMID in
#: `phenotype_terms[].models[].pmids[]` as a four-digit-or-more count and gave
#: it thousands separators, so `11911824` became `11,911,824`. `app.js` builds
#: every PubMed href as the base plus that value, with no validation, so all 12
#: PubMed links in the tool resolved to an empty `[uid]` search. It shipped in
#: 9f19811 and the live site served it until the link check of 2026-10-21.
#:
#: `wording._BARE_IDENTIFIER` now stops this at the generator, so this set is
#: belt and braces rather than the fix. It is deliberately only the two PubMed
#: keys: every other identifier in these artefacts carries a letter prefix
#: (`ENSG...`, `NCT...`, `CHEMBL...`, `MP:...`, `E-GEOD-...`), which
#: `_UNGROUPED_COUNT` already exempts, and widening this set past the evidence
#: is how the grouping rule got into trouble in the first place.
ID_KEYS = frozenset({"pmid", "pmids"})

#: An identifier that a previous restamp already grouped. The repair is on the
#: artefacts, not on `modernise`: the commas are on disk in the four baked runs
#: and in the published export, and the generator fix cannot reach back and
#: remove them.
_GROUPED_IDENTIFIER = re.compile(r"\A\s*\d{1,3}(?:,\d{3})+\s*\Z")

#: Sentences this build rewords, as exact pairs. The code strip above is a
#: rule; these are the two *judgements* of the 2026-10-20 review that landed in
#: a baked invariant detail, and a judgement is not something to infer from a
#: regex -- so each one is written out, with the reason, and applied only where
#: its exact old text is present.
#:
#: Each pair changes CONSTANT words only. The numbers in these sentences are
#: the run's own and are outside every pattern, which `restamp` asserts: the
#: digits in a string before and after a migration are identical.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "I7 read as though a ranking existed for it to survive, under a "
        "sub-header saying 'Nothing here is ranked or scored'",
        "no headline marker survives the ranking; ",
        "no headline marker is drawn on the figure, and nothing is ranked or "
        "scored to put one there; ",
    ),
    (
        "I5 said 'checked live' while every drawer said 'replayed from cache'. "
        "Both were true of DIFFERENT calls -- the integrity probe bypasses the "
        "cache by construction, the per-gene counts do not -- and neither "
        "sentence said so, so a reader meeting both concluded one was lying",
        " Europe PMC echo(es) checked live and byte-identical",
        " Europe PMC integrity probe echo(es) checked live and byte-identical",
    ),
    (
        "the same, second half: the clause that names the calls that were NOT "
        "checked live, in the place `_i5_literature` now writes it",
        "byte-identical; no ERROR key in",
        "byte-identical; the per-gene Europe PMC counts in the drawers are "
        "read from this machine's cache and each says so; no ERROR key in",
    ),
)


def _numbers(text: str) -> list[str]:
    """Every number in `text`, grouping removed, in order."""
    return [m.replace(",", "") for m in re.findall(r"\d[\d,]*", text)]


def _reword(text: str) -> str:
    """`strip_internal_codes`, then the explicit migrations.

    The migrations never touch a digit, asserted here rather than trusted: this
    is a wording pass on a finished analysis, and a number moving in it would
    be the plausible-wrong output AGENTS.md's one rule is about.
    """
    out = modernise(text)
    # The digit assertion is on the MIGRATIONS, not on the strip: a code is
    # partly a number ("COUNCIL V13"), so removing one removes digits. A
    # migration rewords constant text around the run's own numbers, and a digit
    # moving in one would be the plausible-wrong output AGENTS.md's one rule is
    # about -- so it is asserted rather than trusted.
    reworded = out
    for _why, old, new in MIGRATIONS:
        if old in reworded:
            reworded = reworded.replace(old, new)
    # The NUMBERS must be the same numbers. Compared as integers with the
    # grouping removed, because `modernise` inserts thousands separators -- a
    # format change -- while a migration must never move a value.
    assert _numbers(out) == _numbers(reworded), (out, reworded)
    return reworded


def _repair_identifiers(node: Any, hits: list[tuple[str, str]],
                        path: str) -> Any:
    """An id-bearing value with a previous pass's thousands separators out.

    Never reworded: `modernise` does not run here at all. Only a value that is
    nothing but grouped digits is touched, so this cannot reach an identifier
    carrying a letter even if one is filed under an id key by mistake.

    >>> hits = []
    >>> _repair_identifiers(["11,911,824", "9892619"], hits, ".pmids")
    ['11911824', '9892619']
    >>> hits
    [('.pmids[0]', '11,911,824')]
    >>> _repair_identifiers("CHEMBL1742997", [], ".drug_id")
    'CHEMBL1742997'
    >>> _repair_identifiers("20643340", [], ".pmids")
    '20643340'
    """
    if isinstance(node, str):
        if _GROUPED_IDENTIFIER.match(node):
            hits.append((path, node))
            return node.strip().replace(",", "")
        return node
    if isinstance(node, list):
        return [_repair_identifiers(v, hits, f"{path}[{i}]")
                for i, v in enumerate(node)]
    return node


def _walk(node: Any, hits: list[tuple[str, str]], path: str = "") -> Any:
    """`node` with every readable string reworded, collecting what changed."""
    if isinstance(node, str):
        new = _reword(node)
        if new != node:
            hits.append((path, node))
        return new
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            where = f"{path}.{k}"
            if k in OPAQUE_KEYS and not (
                    k in BY_SCALAR_ONLY and isinstance(v, (dict, list))):
                out[k] = v
            elif k in ID_KEYS:
                out[k] = _repair_identifiers(v, hits, where)
            else:
                out[k] = _walk(v, hits, where)
        return out
    if isinstance(node, list):
        return [_walk(v, hits, f"{path}[{i}]") for i, v in enumerate(node)]
    return node


def _rewrite_json(path: Path, *, write: bool) -> list[tuple[str, str]]:
    doc = json.loads(path.read_text())
    hits: list[tuple[str, str]] = []
    out = _walk(doc, hits)
    if hits and write:
        _atomic(path, json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return hits


def _rewrite_text(path: Path, *, write: bool) -> list[tuple[str, str]]:
    """`values.csv`, `ledger.csv`, `caption.txt`: one LINE at a time.

    Deliberately not parsed. `values.csv` carries appended `# SPOKE ORDER`,
    `# FILTERS ACTIVE` and `# DROP LEDGER` sections after the cell rows, and
    `tests/test_provenance.py` holds that `table.read_cell_rows` is the only
    thing that reads it as cells -- a csv reader here would read past the
    boundary and a csv writer would re-emit the comment rows as data.

    A wording fix does not need the structure: `strip_internal_codes` removes
    code tokens and closes the seam, and it returns a line with no code in it
    BY IDENTITY. So every byte of every untouched line survives, including the
    quoting, the blank separator lines and the comment rows.
    """
    lines = path.read_text().splitlines(keepends=True)
    hits: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        new = _reword(line)
        if new != line:
            hits.append((f"line {i + 1}", line.strip()))
            lines[i] = new
    if hits and write:
        _atomic(path, "".join(lines))
    return hits


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def restamp(run_id: str, *, write: bool = True) -> dict[str, Any]:
    """One run. Returns {"run_id", "files": {name: n_changes}, "examples"}."""
    run_dir = table_mod.run_dir(run_id)
    if not run_dir.is_dir():
        raise SystemExit(f"no such run: {run_id}")
    # The run directory's own times are put back afterwards, as `bake_run` and
    # `restamp_recommendations` do: the artefacts are dated by the manifest and
    # an mtime bump would make a wording fix look like a re-run.
    st = run_dir.stat()
    dir_times = (st.st_atime_ns, st.st_mtime_ns)
    out: dict[str, Any] = {"run_id": run_id, "files": {}, "examples": []}
    try:
        targets = [p for p in (run_dir / "manifest.json", run_dir / "values.csv",
                               run_dir / "ledger.csv", run_dir / "caption.txt")
                   if p.is_file()]
        targets += sorted((run_dir / "drawer").glob("*.json"))
        for p in targets:
            hits = (_rewrite_json(p, write=write) if p.suffix == ".json"
                    else _rewrite_text(p, write=write))
            if hits:
                out["files"][p.relative_to(run_dir).as_posix()] = len(hits)
                out["examples"].extend(hits[:1])
    finally:
        os.utime(run_dir, ns=dir_times)
    return out


def main(argv: list[str]) -> int:
    write = "--check" not in argv
    runs = [a for a in argv if not a.startswith("-")] or list(config.DEMO_RUNS)
    total = 0
    for run_id in runs:
        res = restamp(run_id, write=write)
        n = sum(res["files"].values())
        total += n
        print(f"{run_id}: {n} string(s) in {len(res['files'])} file(s)"
              + ("" if write else "  [--check, nothing written]"))
        for name, k in sorted(res["files"].items()):
            print(f"  {name}: {k}")
        for _, before in res["examples"][:3]:
            print(f"    was: {before[:90]}")
    print(f"\n{total} string(s) rewritten" if write
          else f"\n{total} string(s) would change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
