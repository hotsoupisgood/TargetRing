"""DE input contract. One test per row of QA B7's table."""

from __future__ import annotations

import pytest

from targetring.de_input import (
    DERow,
    InputRejected,
    LedgerEntry,
    assert_accounted,
    dedupe_on_resolved,
    decode,
    parse_de_table,
)

HEAD = "gene,logFC,padj\n"


def statuses(parsed) -> dict[str, int]:
    return parsed.status_counts


class TestAccounting:
    """The governing invariant: nothing vanishes."""

    def test_every_row_is_rendered_or_ledgered(self) -> None:
        csv_text = HEAD + "\n".join([
            "BRAF,2.1,0.001",
            "TP53,-1.4,0.02",
            "NOGENE,1.0,",          # no padj
            "BADFC,NA,0.01",        # bad logFC
            ",1.0,0.01",            # blank symbol
            "9-Sep,1.2,0.003",      # excel, still a parseable row
        ])
        p = parse_de_table(csv_text)
        assert p.input_row_count == 6
        assert_accounted(p.input_row_count, len(p.rows), p.ledger)

    def test_assert_catches_a_lost_gene(self) -> None:
        with pytest.raises(AssertionError, match="accounting broken"):
            assert_accounted(10, 8, [LedgerEntry("X", "unresolved", "r")])


class TestPadj:
    """QA B7: never coerce a blank padj. 0 ranks it top, 1 buries it."""

    @pytest.mark.parametrize("bad", ["", "NA", "n/a", "NULL", "#DIV/0!"])
    def test_blank_padj_rejected_not_coerced(self, bad: str) -> None:
        p = parse_de_table(HEAD + f"BRAF,2.1,{bad}")
        assert p.rows == []
        assert p.ledger[0].status == "dropped_no_padj"
        assert "not coerced" in p.ledger[0].reason

    @pytest.mark.parametrize("bad", ["1.5", "-0.1"])
    def test_out_of_range_padj_rejected(self, bad: str) -> None:
        p = parse_de_table(HEAD + f"BRAF,2.1,{bad}")
        assert p.rows == []
        assert p.ledger[0].status == "dropped_no_padj"


class TestLogFC:
    @pytest.mark.parametrize("bad", ["NA", "#DIV/0!", "Inf", "-Inf", "nan", ""])
    def test_non_finite_logfc_rejected(self, bad: str) -> None:
        p = parse_de_table(HEAD + f"BRAF,{bad},0.01")
        assert p.rows == []
        assert p.ledger[0].status == "dropped_bad_logfc"

    def test_negative_and_zero_are_fine(self) -> None:
        p = parse_de_table(HEAD + "A,-2.5,0.01\nB,0,0.02")
        assert [r.logfc for r in p.rows] == [-2.5, 0.0]


class TestCommaDecimals:
    def test_whole_column_comma_is_coerced(self) -> None:
        p = parse_de_table(HEAD + "BRAF,\"2,1\",\"0,001\"\nTP53,\"-1,4\",\"0,02\"")
        assert [r.logfc for r in p.rows] == [2.1, -1.4]
        assert [r.padj for r in p.rows] == [0.001, 0.02]

    def test_mixed_column_rejects_the_file(self) -> None:
        """Guessing per row is how you get 123 from 1,23."""
        with pytest.raises(InputRejected, match="mixes comma and dot"):
            parse_de_table(HEAD + "BRAF,\"2,1\",0.001\nTP53,-1.4,0.02")


class TestHeadersAndEncoding:
    def test_missing_header_rejects_file(self) -> None:
        with pytest.raises(InputRejected, match="No adjusted p-value column"):
            parse_de_table("gene,logFC\nBRAF,2.1")

    def test_positional_guessing_is_not_attempted(self) -> None:
        with pytest.raises(InputRejected, match="Positional guessing is not attempted"):
            parse_de_table("a,b,c\nBRAF,2.1,0.01")

    def test_bom_stripped(self) -> None:
        p = parse_de_table(("﻿" + HEAD + "BRAF,2.1,0.01").encode("utf-8"))
        assert p.rows[0].symbol == "BRAF"

    def test_utf16(self) -> None:
        p = parse_de_table((HEAD + "BRAF,2.1,0.01").encode("utf-16"))
        assert p.rows[0].symbol == "BRAF"

    def test_undecodable_is_named_not_replaced(self) -> None:
        """Latin-1 maps every byte, so a naive fallback chain never rejects and
        turns rubbish into mojibake symbols instead. 0x81/0x8D are invalid UTF-8
        and land on C1 controls in Latin-1 -- that must be an error, not a gene.
        """
        with pytest.raises(InputRejected, match="Could not decode"):
            decode(b"gene,logFC,padj\n\x81\x8d\x8f,2.1,0.01")

    def test_an_oversize_field_is_a_refusal_not_a_csv_error(self) -> None:
        """`csv` raises `_csv.Error: field larger than field limit (131072)`
        from inside `list(reader)`. Found 2026-10-18 reaching `/api/analyse`'s
        catch-all as a 500 with a traceback; it is a fact about the file."""
        import csv
        blob = "x" * (csv.field_size_limit() + 10)
        with pytest.raises(InputRejected, match="could not be read as CSV"):
            parse_de_table(HEAD + f'BRAF,2.1,0.01\n"{blob}",1.0,0.5\n')

    def test_an_unclosed_quote_in_the_header_is_the_same_refusal(self) -> None:
        import csv
        blob = "y" * (csv.field_size_limit() + 10)
        with pytest.raises(InputRejected, match="could not be read as CSV"):
            parse_de_table(f'"{blob},logFC,padj\nBRAF,2.1,0.01\n')

    def test_real_latin1_still_accepted(self) -> None:
        p = parse_de_table("gene,logFC,padj\nBRAF,2.1,0.01\n".encode("latin-1"))
        assert p.rows[0].symbol == "BRAF"

    def test_tab_separated(self) -> None:
        p = parse_de_table("gene\tlogFC\tpadj\nBRAF\t2.1\t0.01\nTP53\t-1\t0.02")
        assert len(p.rows) == 2

    def test_alternate_header_names(self) -> None:
        p = parse_de_table("Symbol,log2FoldChange,FDR\nBRAF,2.1,0.01")
        assert p.rows[0].symbol == "BRAF"

    def test_quotes_and_whitespace_stripped(self) -> None:
        p = parse_de_table(HEAD + '"  BRAF  ",2.1,0.01')
        assert p.rows[0].symbol == "BRAF"


class TestCap:
    def test_over_cap_genes_are_named_in_the_ledger(self) -> None:
        rows = "\n".join(f"G{i},1.0,{i / 1000:.4f}" for i in range(1, 21))
        p = parse_de_table(HEAD + rows, max_genes=5)
        assert len(p.rows) == 5
        over = [e for e in p.ledger if e.status == "dropped_over_cap"]
        assert len(over) == 15
        assert_accounted(p.input_row_count, len(p.rows), p.ledger)
        # kept the strongest padj, and said so
        assert {r.symbol for r in p.rows} == {"G1", "G2", "G3", "G4", "G5"}


class TestDegenerate:
    @pytest.mark.parametrize("n", [1, 2])
    def test_tiny_gene_sets_parse(self, n: int) -> None:
        rows = "\n".join(f"G{i},1.0,0.01" for i in range(n))
        p = parse_de_table(HEAD + rows)
        assert len(p.rows) == n


class TestDedupe:
    def test_dedupe_on_resolved_id_keeps_max_abs_logfc(self) -> None:
        """`SEPT9` and `SEPTIN9` are one gene, so they must collapse to one spoke."""
        rows = [
            DERow(1, "SEPT9", 1.0, 0.01),
            DERow(2, "SEPTIN9", -3.0, 0.02),
            DERow(3, "BRAF", 2.0, 0.03),
        ]
        key = {1: "ENSG00000184640", 2: "ENSG00000184640", 3: "ENSG00000157764"}
        kept, drops = dedupe_on_resolved(rows, key)
        assert len(kept) == 2
        assert {r.symbol for r in kept} == {"SEPTIN9", "BRAF"}
        assert len(drops) == 1
        assert drops[0].status == "dropped_dup"
        assert "kept logFC=-3" in drops[0].reason
        assert "dropped logFC=1" in drops[0].reason
