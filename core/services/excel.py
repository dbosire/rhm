"""Excel generation: new 13-column workbooks and population of uploaded templates."""
import io
import re
from copy import copy
from datetime import date, datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.cell_range import CellRange

from core.constants import (
    EXCEL_DATE_FORMAT, EXCEL_MONEY_FORMAT, HEADER_SYNONYMS, MONEY_PARAMS, PARAM_KEYS, PARAM_LABELS,
    STANDARD_PARAMETERS, TEXT_PARAMS,
)

from .excel_safety import open_safe_workbook, safe_text

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STRIPE_FILL = PatternFill("solid", fgColor="EEF3F9")
THIN = Side(style="thin", color="B4C6E7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
INVALID_SHEET_CHARS = re.compile(r"[\[\]\:\*\?\/\\]")


def clean_sheet_name(name, default="Extracted Transactions"):
    name = INVALID_SHEET_CHARS.sub(" ", (name or "").strip())[:31].strip("' ")
    return name or default


def excel_value(txn, key):
    """Value for one parameter, typed correctly for Excel."""
    v = getattr(txn, key)
    if v in (None, ""):
        return None
    if key == "submission_date":  # date only (MM/DD/YY), no time
        return (timezone.localtime(v) if timezone.is_aware(v) else v).date()
    if key in MONEY_PARAMS or key == "quantity":
        return float(Decimal(v))
    return safe_text(v)


def _apply_format(cell, key):
    if key in MONEY_PARAMS:
        cell.number_format = EXCEL_MONEY_FORMAT
    elif key == "quantity":
        cell.number_format = "#,##0.##"
    elif key == "submission_date":
        cell.number_format = EXCEL_DATE_FORMAT
    elif key in TEXT_PARAMS:
        cell.number_format = "@"
        cell.data_type = "s"


# ------------------------------------------------------------------ new workbook
def build_new_workbook(transactions, sheet_name="Extracted Transactions", include_total=True) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = clean_sheet_name(sheet_name)
    headers = [label for _, label in STANDARD_PARAMETERS]
    ws.append(headers)
    for col, _ in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 22

    widths = [len(h) + 2 for h in headers]
    row = 1
    for row, txn in enumerate(transactions, start=2):
        for col, key in enumerate(PARAM_KEYS, start=1):
            val = excel_value(txn, key)
            c = ws.cell(row=row, column=col, value=val)
            _apply_format(c, key)
            c.border = BORDER
            if row % 2 == 1:
                c.fill = STRIPE_FILL
            shown = val.strftime("%m/%d/%y") if isinstance(val, (date, datetime)) else (
                f"{val:,.2f}" if isinstance(val, float) else str(val or ""))
            widths[col - 1] = max(widths[col - 1], len(shown) + 2)
    last = row
    if last >= 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last}"
    else:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    ws.freeze_panes = "A2"

    if include_total and last >= 2:
        amount_col = PARAM_KEYS.index("amount") + 1
        tr = last + 1
        label = ws.cell(row=tr, column=amount_col - 1, value="Total")
        total = ws.cell(row=tr, column=amount_col, value=f"=SUM({get_column_letter(amount_col)}2:{get_column_letter(amount_col)}{last})")
        for c in (label, total):
            c.font = Font(bold=True)
            c.border = Border(top=Side(style="medium", color="1F4E79"), bottom=Side(style="double", color="1F4E79"))
        total.number_format = EXCEL_MONEY_FORMAT

    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(w, 10), 60)
    ws.sheet_view.zoomScale = 100
    wb.properties.creator = "RHM SMS Extractor"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ template inspection
def load_template(file_field, read_only=False):
    file_field.open("rb")
    try:
        data = io.BytesIO(file_field.read())
    finally:
        file_field.close()
    return open_safe_workbook(data, read_only=read_only, data_only=False)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def guess_header_row(ws, max_scan=15):
    best_row, best_score = 1, -1
    synonyms = {_norm(s) for vals in HEADER_SYNONYMS.values() for s in vals}
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True), start=1):
        texts = [_norm(v) for v in row if isinstance(v, str) and v.strip()]
        score = sum(1 for t in texts if t in synonyms) * 3 + len(texts)
        if score > best_score and texts:
            best_row, best_score = r_idx, score
    return best_row


def read_headers(ws, header_row):
    headers = {}
    for cell in ws[header_row]:
        if isinstance(cell, MergedCell):
            continue
        if cell.value not in (None, ""):
            headers[cell.column_letter] = str(cell.value).strip()
    return headers


def auto_map(headers: dict) -> dict:
    mapping, used = {}, set()
    for key in PARAM_KEYS:
        options = [_norm(PARAM_LABELS[key])] + [_norm(s) for s in HEADER_SYNONYMS.get(key, [])]
        for opt in options:
            hit = next((col for col, h in headers.items() if _norm(h) == opt and col not in used), None)
            if hit:
                mapping[key] = hit
                used.add(hit)
                break
    return mapping


def preview_rows(ws, max_rows=25, max_cols=26):
    rows = []
    max_c = min(ws.max_column or 1, max_cols)
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, max_rows), max_col=max_c):
        out = []
        for c in row:
            v = c.value
            if isinstance(v, datetime):
                v = v.strftime("%d/%m/%Y %H:%M")
            elif isinstance(v, float):
                v = f"{v:,.2f}"
            out.append("" if v is None else str(v))
        rows.append(out)
    return rows, [get_column_letter(i) for i in range(1, max_c + 1)]


# ------------------------------------------------------------------ template population
REF_RE = re.compile(
    r"(?<![A-Za-z0-9_!'\"\.])(\$?)([A-Z]{1,3})(\$?)(\d+)(?::(\$?)([A-Z]{1,3})(\$?)(\d+))?(?![A-Za-z0-9_(])"
)


def shift_formula(formula: str, insert_at: int, n: int) -> str:
    """Shift row references at/after insert_at by n, and expand ranges that end on the
    last existing data row (insert_at-1) so totals include the new rows."""

    def repl(m):
        c1a, c1, r1a, r1, c2a, c2, r2a, r2 = m.groups()
        r1 = int(r1)
        if c2 is None:
            nr1 = r1 + n if r1 >= insert_at else r1
            return f"{c1a}{c1}{r1a}{nr1}"
        r2 = int(r2)
        nr1 = r1 + n if r1 >= insert_at else r1
        if r2 >= insert_at:
            nr2 = r2 + n
        elif r2 == insert_at - 1 and r1 < insert_at:
            nr2 = r2 + n
        else:
            nr2 = r2
        return f"{c1a}{c1}{r1a}{nr1}:{c2a}{c2}{r2a}{nr2}"

    # leave string literals untouched
    parts = re.split(r'("[^"]*")', formula)
    return "".join(p if p.startswith('"') else REF_RE.sub(repl, p) for p in parts)


def _is_total_row(ws, r, max_col):
    for c in range(1, max_col + 1):
        v = ws.cell(row=r, column=c).value
        if isinstance(v, str) and re.match(r"^\s*(grand\s+)?totals?\b", v, re.I):
            return True
    return False


def _row_has_values(ws, r, cols):
    return any(ws.cell(row=r, column=c).value not in (None, "") for c in cols)


def find_insert_row(ws, data_start_row, mapped_cols):
    max_row = ws.max_row or data_start_row
    max_col = ws.max_column or 1
    last_data = data_start_row - 1
    for r in range(data_start_row, max_row + 1):
        if _is_total_row(ws, r, max_col):
            break
        if _row_has_values(ws, r, mapped_cols):
            last_data = r
    return last_data + 1, last_data


def _copy_style(src, dst):
    if src.has_style:
        dst.font = copy(src.font)
        dst.fill = copy(src.fill)
        dst.border = copy(src.border)
        dst.alignment = copy(src.alignment)
        dst.number_format = src.number_format
        dst.protection = copy(src.protection)


def populate_template(file_field, sheet_name, header_row, data_start_row, mapping: dict, transactions) -> tuple[bytes, int]:
    """Write transactions into a copy of the template. The original file is never modified.

    Returns (xlsx_bytes, first_row_written).
    """
    wb = load_template(file_field)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Worksheet '{sheet_name}' not found in template.")
    ws = wb[sheet_name]
    mapping = {k: v for k, v in mapping.items() if k in PARAM_KEYS and v}
    if not mapping:
        raise ValueError("Map at least one parameter to a column.")
    mapped_cols = sorted({column_index_from_string(c) for c in mapping.values()})
    data_start_row = max(int(data_start_row), int(header_row) + 1)
    txns = list(transactions)
    n = len(txns)
    insert_at, last_data = find_insert_row(ws, data_start_row, mapped_cols)
    max_col = max(ws.max_column or 1, max(mapped_cols))

    # Style reference: last existing data row, else the first data row
    style_row = last_data if last_data >= data_start_row else data_start_row
    style_cells = {c: copy(ws.cell(row=style_row, column=c)) for c in range(1, max_col + 1)}
    style_height = ws.row_dimensions[style_row].height

    has_content_below = any(
        _row_has_values(ws, r, range(1, max_col + 1)) for r in range(insert_at, (ws.max_row or insert_at) + 1)
    )
    shift = n if has_content_below else 0
    if shift:
        ws.insert_rows(insert_at, amount=n)
        # shift merged ranges below the insertion point
        for mr in list(ws.merged_cells.ranges):
            if mr.min_row >= insert_at:
                ws.merged_cells.ranges.remove(mr)
                ws.merged_cells.ranges.add(CellRange(min_col=mr.min_col, min_row=mr.min_row + n,
                                                      max_col=mr.max_col, max_row=mr.max_row + n))
        # shift row heights
        heights = {r: d.height for r, d in ws.row_dimensions.items() if r >= insert_at and d.height}
        for r in sorted(heights, reverse=True):
            ws.row_dimensions[r + n].height = heights[r]
            ws.row_dimensions[r].height = None

    # Fix formulas across the workbook that point at this sheet's rows (same sheet only)
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                c.value = shift_formula(c.value, insert_at, shift) if shift else _expand_only(c.value, insert_at, n)

    # Write the data
    for i, txn in enumerate(txns):
        r = insert_at + i
        if style_height:
            ws.row_dimensions[r].height = style_height
        for c_idx in range(1, max_col + 1):
            src = style_cells.get(c_idx)
            if src is not None and last_data >= data_start_row:
                _copy_style(src, ws.cell(row=r, column=c_idx))
        for key, col in mapping.items():
            cell = ws.cell(row=r, column=column_index_from_string(col))
            cell.value = excel_value(txn, key)
            if key in MONEY_PARAMS and cell.number_format in ("General", "@"):
                cell.number_format = EXCEL_MONEY_FORMAT
            elif key == "submission_date":
                cell.number_format = EXCEL_DATE_FORMAT
            elif key in TEXT_PARAMS:
                cell.number_format = "@"
                if cell.value is not None:
                    cell.data_type = "s"

    # Expand an existing auto-filter to cover the new rows
    if ws.auto_filter.ref:
        try:
            rng = CellRange(ws.auto_filter.ref)
            if rng.max_row >= insert_at - 1:
                rng.expand(down=n) if rng.max_row >= last_data else None
                ws.auto_filter.ref = rng.coord
        except Exception:
            pass

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), insert_at


def _expand_only(formula, insert_at, n):
    """No rows shifted: only grow ranges ending on the last data row."""

    def repl(m):
        c1a, c1, r1a, r1, c2a, c2, r2a, r2 = m.groups()
        if c2 is None:
            return m.group(0)
        r1, r2 = int(r1), int(r2)
        if r2 == insert_at - 1 and r1 < insert_at:
            r2 += n
        return f"{c1a}{c1}{r1a}{r1}:{c2a}{c2}{r2a}{r2}"

    parts = re.split(r'("[^"]*")', formula)
    return "".join(p if p.startswith('"') else REF_RE.sub(repl, p) for p in parts)


def to_contentfile(data: bytes, name="report.xlsx"):
    return ContentFile(data, name=name)
