# fixtures/atlas — how these files were cut

Written by `scripts/make_atlas_fixtures.py` from the warm `cache/atlas.*`
entries of a live `scripts/build_atlas_index.py` run. No test in this repo
reaches the network; live probes live in `scripts/`.

| file | original | here | how |
|---|---|---|---|
| `E-GEOD-101794-configuration.xml` | 14,141 B | 14,141 B | copied whole |
| `E-GEOD-101794.condensed-sdrf.tsv` | 223,694 B | 223,694 B | copied whole |
| `E-GEOD-6731-configuration.xml` | 2,321 B | 2,321 B | copied whole |
| `E-GEOD-6731.condensed-sdrf.tsv` | 23,170 B | 23,170 B | copied whole |
| `E-GEOD-57945-configuration.xml` | 16,457 B | 16,457 B | copied whole |
| `E-GEOD-57945.condensed-sdrf.tsv` | 245,353 B | 245,353 B | copied whole |
| `E-GEOD-100833-configuration.xml` | 146,353 B | 7,227 B | first 4 assay groups and the contrasts between them; SDRF cut to their 114 assays plus 3 unrelated ones |
| `E-GEOD-100833.condensed-sdrf.tsv` | 2,561,128 B | 171,289 B | cut to match |
| `E-FAKE-1-*` | — | — | HAND-WRITTEN: a contrast whose id is not `{ref}_{test}`, which eight real Atlas contrasts are |
| `index/crohns-index.json` | 1,126 contrasts | 166 | the real index restricted to the 17 experiments with a Crohn's-tagged test group |
| `ols-cache/` | — | 48 entries | the live OLS4 payloads as a `cache.Cache` tree, so tests use the real cache key |
