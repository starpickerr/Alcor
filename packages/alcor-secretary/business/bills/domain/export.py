"""Render ledger exports inside the Bills data owner."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from .service import parse_occurred_at


_HEADERS = {
    "zh": ("日期", "时间", "类型", "名称", "类别", "金额", "币种", "备注", "状态"),
    "en": ("Date", "Time", "Type", "Name", "Category", "Amount", "Currency", "Note", "Status"),
}

_TYPE = {
    "zh": {"income": "收入", "expense": "支出"},
    "en": {"income": "Income", "expense": "Expense"},
}

_STATUS = {
    "zh": {"posted": "已入账", "pending": "待入账", "reversed": "已冲销"},
    "en": {"posted": "Posted", "pending": "Pending", "reversed": "Reversed"},
}

_SHEET = {"zh": "明细", "en": "Ledger"}
_TITLE = {"zh": "账单明细 {start} ~ {end}", "en": "Ledger {start} ~ {end}"}
_INCOME = {"zh": "收入合计", "en": "Income total"}
_EXPENSE = {"zh": "支出合计", "en": "Expense total"}
_NET = {"zh": "结余", "en": "Net"}


def _lang(value: str) -> str:
    return "en" if str(value or "").lower().startswith("en") else "zh"


def _cell_text(row: int, col: int, text: str) -> str:
    ref = f"{chr(ord('A') + col)}{row}"
    return (
        f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
        f"{escape(text)}</t></is></c>"
    )


def _cell_num(row: int, col: int, value: str) -> str:
    ref = f"{chr(ord('A') + col)}{row}"
    return f'<c r="{ref}" t="n"><v>{value}</v></c>'


def _row_xml(r: int, cells: str) -> str:
    return f'<row r="{r}">{cells}</row>'


def _money(amount_minor: int, currency: str) -> str:
    if currency == 'JPY':
        return str(amount_minor)
    whole, fraction = divmod(abs(amount_minor), 100)
    return f"{'-' if amount_minor < 0 else ''}{whole}.{fraction:02d}"


def _wall_parts(occurred_at: str) -> tuple[str, str]:
    dt = parse_occurred_at(occurred_at)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def build_ledger_xlsx(entries: list, start: str, end: str, lang: str = "zh") -> bytes:
    loc = _lang(lang)
    headers = _HEADERS[loc]
    title = _TITLE[loc].format(start=start, end=end)
    rows_xml = [
        _row_xml(1, _cell_text(1, 0, title)),
        _row_xml(2, "".join(_cell_text(2, i, h) for i, h in enumerate(headers))),
    ]

    totals = {}
    r = 3
    for entry in entries:
        day, clock = _wall_parts(entry["occurred_at"])
        entry_type = entry.get("entry_type") or ""
        amount_minor = int(entry.get("amount_minor") or 0)
        signed = amount_minor if entry_type == "income" else -amount_minor
        status = entry.get("status") or ""
        currency = str(entry.get('currency') or 'CNY')
        total = totals.setdefault(currency, {'income': 0, 'expense': 0})
        if status == 'posted' and entry_type in total:
            total[entry_type] += amount_minor
        values = [
            day,
            clock,
            _TYPE[loc].get(entry_type, entry_type),
            str(entry.get("name") or ""),
            str(entry.get("category") or ""),
            None,
            str(entry.get("currency") or "CNY"),
            str(entry.get("note") or ""),
            _STATUS[loc].get(status, status),
        ]
        cells = []
        for i, value in enumerate(values):
            if i == 5:
                cells.append(_cell_num(r, i, _money(signed, currency)))
            else:
                cells.append(_cell_text(r, i, value))
        rows_xml.append(_row_xml(r, "".join(cells)))
        r += 1

    r += 1
    for currency, total in totals.items():
        for label, amount in ((_INCOME[loc], total['income']), (_EXPENSE[loc], -total['expense']), (_NET[loc], total['income'] - total['expense'])):
            r += 1
            rows_xml.append(_row_xml(r, _cell_text(r, 3, label) + _cell_num(r, 5, _money(amount, currency)) + _cell_text(r, 6, currency)))

    sheet_name = _SHEET[loc]
    last_row = r
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:I{last_row}"/>'
        "<sheetData>"
        f"{''.join(rows_xml)}"
        "</sheetData></worksheet>"
    )

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def build_ledger_csv(entries):
    import csv
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['时间', '类型', '金额', '币种', '分类', '名称', '对方', '备注', '状态'])
    def safe(value):
        text = str(value or '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text
    for entry in entries:
        amount = entry['amount_minor']
        values = [entry['occurred_at'], _TYPE['zh'][entry['entry_type']],
                  _money(amount, entry['currency']), entry['currency'],
                  entry['category'], entry['name'], entry['counterparty'],
                  entry['note'], _STATUS['zh'][entry['status']]]
        writer.writerow([safe(value) for value in values])
    return out.getvalue()
