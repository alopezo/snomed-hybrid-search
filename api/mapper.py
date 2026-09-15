#!/usr/bin/env python3
"""
Batch mapping: a two-column table (code, term) -> best SNOMED concept per term.

Each `term` is mapped through the SEMANTIC channel only (no lexical, no rerank, no LLM expansion),
which is the right regime for a terminology-to-terminology map: the source terms are already curated
clinical phrases, so paraphrase/synonym matching (BioLORD) is what we want, and rerank/expansion would
only add latency. Two columns are appended to the original table: `snomed_code` (concept_id of the best
match) and `snomed_term` (its FSN). Rows with an empty/unmatched term get empty cells.

Supported formats: .csv, .xlsx and .ods (read and write). Legacy .xls needs xlrd and is out of scope.
"""
from __future__ import annotations

import csv
import io
import os

from openpyxl import Workbook, load_workbook

from api.search import search

NEW_COLS = ("snomed_code", "snomed_term")
SUPPORTED = ("csv", "xlsx", "ods")


def _read_ods(content: bytes) -> list[list[str]]:
    """Read the first sheet of an .ods into a list of string rows (honours repeated cells)."""
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = load(io.BytesIO(content))
    tables = doc.spreadsheet.getElementsByType(Table)
    if not tables:
        return []
    rows: list[list[str]] = []
    for r in tables[0].getElementsByType(TableRow):
        cells: list[str] = []
        for c in r.getElementsByType(TableCell):
            rep = int(c.getAttribute("numbercolumnsrepeated") or 1)
            txt = "".join(str(p) for p in c.getElementsByType(P))
            cells.extend([txt] * rep)
        while cells and cells[-1] == "":   # drop the trailing repeated-empty padding ODS emits
            cells.pop()
        rows.append(cells)
    return rows


def read_table(content: bytes, filename: str) -> tuple[list[str], list[list[str]], str]:
    """Parse an uploaded .csv/.xlsx/.ods into (headers, data_rows, fmt). Blank rows are dropped."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
        fmt = "csv"
    elif name.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        fmt = "xlsx"
    elif name.endswith(".ods"):
        rows = _read_ods(content)
        fmt = "ods"
    else:
        raise ValueError("Unsupported file type. Use .csv, .xlsx or .ods (legacy .xls is not supported).")

    rows = [r for r in rows if any((str(c) or "").strip() for c in r)]  # drop fully-blank rows
    if not rows:
        raise ValueError("The file has no rows.")
    headers = [(str(h) or "").strip() for h in rows[0]]
    return headers, rows[1:], fmt


def resolve_columns(headers: list[str], code_col: str | None = None,
                    term_col: str | None = None) -> tuple[int, int]:
    """Resolve the code/term column indices. Accepts a header name or a 0-based index string; falls
    back to headers literally named 'code'/'term' (case-insensitive), else first/second column."""
    low = [h.lower() for h in headers]

    def find(sel: str | None, default: int) -> int:
        if sel is None or sel == "":
            return default
        if sel in headers:
            return headers.index(sel)
        if sel.lower() in low:
            return low.index(sel.lower())
        try:
            i = int(sel)
            if 0 <= i < len(headers):
                return i
        except (ValueError, TypeError):
            pass
        return default

    code_default = low.index("code") if "code" in low else 0
    term_default = low.index("term") if "term" in low else (1 if len(headers) > 1 else 0)
    return find(code_col, code_default), find(term_col, term_default)


def best_match(term: str, filter_concept: int | None = None, channel: str = "semantic",
               rerank: bool = False) -> tuple[str, str]:
    """(concept_id, FSN) of the top match, or ('', '') if the term is empty / nothing found.

    filter_concept scopes the search to descendants-or-self of that concept (e.g. Observable entity or
    Evaluation procedure for a lab-test catalog), which sharply improves precision when every row belongs
    to one hierarchy. `channel` ("semantic" | "both" | "lexical") and `rerank` let dirtier vocabularies
    lean on lexical fusion and the cross-encoder; gemma expansion stays off (batch mapping, no LLM)."""
    term = (term or "").strip()
    if not term:
        return "", ""
    results = search(term, k=1, use_gemma=False, rerank=rerank, channel=channel,
                     filter_concept=filter_concept).get("results", [])
    if not results:
        return "", ""
    r = results[0]
    return str(r.get("concept_id", "")), (r.get("fsn") or r.get("matched_term") or "")


def _write_ods(cols: list[str], out_rows: list[list[str]], path: str) -> None:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    for r in [cols, *out_rows]:
        tr = TableRow()
        for cell in r:
            tc = TableCell(valuetype="string")
            tc.addElement(P(text=str(cell)))
            tr.addElement(tc)
        table.addElement(tr)
    doc.spreadsheet.addElement(table)
    doc.save(path)


def write_table(headers: list[str], out_rows: list[list[str]], fmt: str, path: str) -> None:
    """Write headers + NEW_COLS and the mapped rows to `path` as csv, xlsx or ods."""
    cols = list(headers) + list(NEW_COLS)
    if fmt == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.append(cols)
        for r in out_rows:
            ws.append(r)
        wb.save(path)
    elif fmt == "ods":
        _write_ods(cols, out_rows, path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(out_rows)
