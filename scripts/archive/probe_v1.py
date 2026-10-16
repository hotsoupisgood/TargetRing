"""One-off pre-build probe. Re-verifies what v1 is about to depend on.

The reviews verified most of this on 2026-09-23/24. This script re-checks the
calls v1 actually issues (data version may have moved) and probes the two
things the reviews explicitly left UNVERIFIED: symbol -> UniProt and
UniProt -> ChEMBL target.

Not part of the app. Run: .venv/bin/python scripts/probe_v1.py
"""

import json
import sys
import time

import httpx

OT = "https://api.platform.opentargets.org/api/v4/graphql"
GTEX = "https://gtexportal.org/api/v2"
PUBTATOR = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"

UA = {"User-Agent": "targetring-probe/0.1 (local research prototype)"}


def show(label, ok, detail):
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {label}: {detail}")


def gql(client, query):
    t = time.time()
    r = client.post(OT, json={"query": query}, headers=UA, timeout=60)
    return r, time.time() - t


def main():
    with httpx.Client(follow_redirects=True) as c:
        # 1. Production OT single-target query
        q = """{target(ensemblId:"ENSG00000157764"){
          approvedSymbol id biotype
          prioritisation{items{key value}}
          tractability{label modality value}
          safetyLiabilities{event datasource}
          geneticConstraint{constraintType oe upperBin6}
          isEssential
          depMapEssentiality{tissueName}
          proteinIds{id source}
          obsoleteSymbols{label}
          mousePhenotypes{modelPhenotypeLabel targetInModelMgiId}
          drugAndClinicalCandidates{count rows{
            maxClinicalStage
            drug{id name drugType maximumClinicalStage}
            diseases{diseaseFromSource disease{id name}}
            clinicalReports{trialPhase clinicalStage trialOverallStatus trialStartDate
                            trialStopReasonCategories source url}
          }}
        }}"""
        r, dt = gql(c, q)
        body = r.json()
        ok = r.status_code == 200 and "errors" not in body and "syntaxError" not in body
        show("OT production query (BRAF)", ok, f"HTTP {r.status_code} {dt:.2f}s {len(r.content)}B")
        if not ok:
            print(json.dumps(body)[:2000])
            return 1
        t = body["data"]["target"]
        prio = {i["key"]: i["value"] for i in t["prioritisation"]["items"]}
        show("  prioritisation keys", len(prio) == 18, f"{len(prio)} keys; value type={type(list(prio.values())[0]).__name__}")
        show("  drugAndClinicalCandidates.count", True, t["drugAndClinicalCandidates"]["count"])
        show("  mousePhenotypes", True, f'{len(t["mousePhenotypes"])} rows')
        show("  safetyLiabilities", True, f'{len(t["safetyLiabilities"])} rows')
        show("  proteinIds (UniProt route, UNVERIFIED in review)", True,
             json.dumps([p for p in t["proteinIds"] if "uniprot" in p["source"].lower()][:4]))
        srcs = {}
        for row in t["drugAndClinicalCandidates"]["rows"]:
            for cr in row["clinicalReports"]:
                srcs[cr["source"]] = srcs.get(cr["source"], 0) + 1
        show("  clinicalReports sources", True, srcs)

        # 2. meta / data version
        r, _ = gql(c, "{meta{apiVersion{x y z} dataVersion{year month iteration}}}")
        show("OT meta", r.status_code == 200, json.dumps(r.json()["data"]["meta"]))

        # 3. mapIds resolver
        terms = ["BRAF", "SEPT9", "MARCH1", "TP53", "9-Sep", "braf", "ENSG00000157764",
                 "C1orf53", "NOTAREALGENE123", "INS", "MIR29B2CHG"]
        r, dt = gql(c, '{mapIds(queryTerms:%s,entityNames:["target"]){mappings{term hits{id name entity}}}}'
                    % json.dumps(terms))
        m = r.json()["data"]["mapIds"]["mappings"]
        show("OT mapIds", r.status_code == 200, f"{dt:.2f}s")
        for mm in m:
            hits = [h for h in mm["hits"] if h["entity"] == "target"]
            print(f"       {mm['term']!r:20} -> {len(hits)} hits  {[(h['id'], h['name']) for h in hits][:3]}")

        # 4. batch drop (B2)
        ids = ["ENSG00000157764", "ENSG00000999999", "ENSG00000141510",
               "ENSG00000012048", "ENSG00000146648"]
        r, dt = gql(c, '{targets(ensemblIds:%s){id approvedSymbol}}' % json.dumps(ids))
        got = r.json()["data"]["targets"]
        show("OT targets() batch drop (B2)", len(got) == 4,
             f"requested 5, returned {len(got)}: {[g['id'] for g in got]}")

        # 5. GTEx
        r = c.get(f"{GTEX}/reference/gene", params={"geneId": "BRAF"}, headers=UA, timeout=60)
        d = r.json()["data"]
        exact = [g for g in d if g.get("geneSymbol") == "BRAF"]
        show("GTEx reference/gene BRAF", r.status_code == 200,
             f'{len(d)} rows; exact={[(g["gencodeId"], g["gencodeVersion"], g["genomeBuild"]) for g in exact]}')
        gid = exact[0]["gencodeId"]
        for dataset in ("gtex_v8", "gtex_v10"):
            r = c.get(f"{GTEX}/expression/medianGeneExpression",
                      params={"gencodeId": gid, "datasetId": dataset, "itemsPerPage": 100},
                      headers=UA, timeout=60)
            try:
                dd = r.json()
                n = len(dd.get("data", []))
                samp = dd["data"][0] if n else None
            except Exception as e:
                n, samp = -1, str(e)[:100]
            show(f"GTEx medianGeneExpression {dataset}", r.status_code == 200 and n > 0,
                 f"HTTP {r.status_code} n_tissues={n} sample={json.dumps(samp)[:160]}")
        # wrong version fails silently
        r = c.get(f"{GTEX}/expression/medianGeneExpression",
                  params={"gencodeId": "ENSG00000157764.14", "datasetId": "gtex_v8"},
                  headers=UA, timeout=60)
        show("GTEx wrong gencode version silently empty", r.status_code == 200,
             f'HTTP {r.status_code} n={len(r.json().get("data", []))}')

        # 6. PubTator3
        for text in ("@GENE_BRAF", '@GENE_BRAF hippocampus'):
            r = c.get(PUBTATOR, params={"text": text}, headers=UA, timeout=60)
            try:
                j = r.json()
                show(f"PubTator3 {text!r}", r.status_code == 200,
                     f'count={j.get("count")} facets={list(j.get("facets", {}).get("facet_fields", {}).keys())}')
            except Exception as e:
                show(f"PubTator3 {text!r}", False, f"HTTP {r.status_code} {str(e)[:120]} body={r.text[:200]!r}")

        # 7. Europe PMC echo (B3)
        for q_ in ("BRAF AND knockout", '"BRAF'):
            r = c.get(EPMC, params={"query": q_, "format": "json", "pageSize": 1},
                      headers=UA, timeout=60)
            j = r.json()
            echo = j.get("request", {}).get("queryString")
            show(f"EuropePMC echo {q_!r}", r.status_code == 200,
                 f'hitCount={j.get("hitCount")} echo={echo!r} identical={echo == q_}')

        # 8. NCBI ERROR-key detection (B3)
        r = c.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                  params={"db": "notadb", "term": "BRAF", "retmode": "json"},
                  headers=UA, timeout=60)
        try:
            j = r.json()
            show("NCBI ERROR-in-200", r.status_code == 200,
                 f'HTTP {r.status_code} ERROR={j.get("esearchresult", {}).get("ERROR")!r}')
        except Exception as e:
            show("NCBI ERROR-in-200", False, f"HTTP {r.status_code} {e}")

        # 9. UNVERIFIED IN REVIEWS: UniProt -> ChEMBL target, then mechanism
        acc = [p["id"] for p in t["proteinIds"] if p["source"].lower().startswith("uniprot")]
        show("UniProt accessions from OT proteinIds", bool(acc), acc[:6])
        for a in acc[:2]:
            r = c.get(f"{CHEMBL}/target.json",
                      params={"target_components__accession": a, "limit": 20},
                      headers=UA, timeout=60)
            j = r.json()
            tgts = j.get("targets", [])
            single = [x for x in tgts if x.get("target_type") == "SINGLE PROTEIN"
                      and x.get("organism") == "Homo sapiens"]
            show(f"ChEMBL target by accession {a}", r.status_code == 200,
                 f'total={j.get("page_meta", {}).get("total_count")} '
                 f'single_protein_human={[x["target_chembl_id"] for x in single]}')
            for x in single[:1]:
                r2 = c.get(f"{CHEMBL}/mechanism.json",
                           params={"target_chembl_id": x["target_chembl_id"],
                                   "only": "molecule_chembl_id,action_type,direct_interaction,"
                                           "disease_efficacy,max_phase,mechanism_of_action",
                                   "limit": 50},
                           headers=UA, timeout=60)
                j2 = r2.json()
                show(f"  ChEMBL mechanism {x['target_chembl_id']}", r2.status_code == 200,
                     f'total={j2.get("page_meta", {}).get("total_count")} '
                     f'first={json.dumps(j2.get("mechanisms", [])[:1])[:220]}')
        # ChEMBL bogus-parameter behaviour (REST 200-on-nonsense risk, QA §4)
        r = c.get(f"{CHEMBL}/target.json", params={"notaparam": "x", "limit": 1},
                  headers=UA, timeout=60)
        show("ChEMBL ignores unknown param (silent-200 risk)", True,
             f'HTTP {r.status_code} total={r.json().get("page_meta", {}).get("total_count") if r.headers.get("content-type","").startswith("application/json") else "n/a"}')

        # 10. a no-record gene for ChEMBL (D5 two-step)
        r = c.get(f"{CHEMBL}/target.json",
                  params={"target_components__accession": "Q9NWM3", "limit": 1},
                  headers=UA, timeout=60)
        show("ChEMBL no target record (TMEM229B/Q9NWM3)", r.status_code == 200,
             f'total={r.json().get("page_meta", {}).get("total_count")}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
