"""Minimal .xlsx writer (stdlib only) for the Signal Desk tracker workbook.

Writes real Excel formulas, styles, column widths, frozen header rows and
filters. The workbook is flagged to recalculate fully when opened, so every
total updates in Excel, LibreOffice and Google Sheets.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape


class F(str):
    """A formula, written without the leading '='."""


# style ids (see STYLES_XML)
DEFAULT, HEADER, IN_NUM, CALC_NUM, PCT, TOTAL, TITLE, IN_TEXT, NOTE, KES, LINK_NUM, TOTAL_KES, SUBHEAD, BIG_KES = range(14)

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="3">
<numFmt numFmtId="164" formatCode="#,##0.00;(#,##0.00);&quot;-&quot;"/>
<numFmt numFmtId="165" formatCode="0.0%;(0.0%);&quot;-&quot;"/>
<numFmt numFmtId="166" formatCode="#,##0;(#,##0);&quot;-&quot;"/>
</numFmts>
<fonts count="8">
<font><sz val="10"/><color rgb="FF000000"/><name val="Arial"/></font>
<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>
<font><sz val="10"/><color rgb="FF0000FF"/><name val="Arial"/></font>
<font><b/><sz val="10"/><color rgb="FF000000"/><name val="Arial"/></font>
<font><b/><sz val="14"/><color rgb="FF111827"/><name val="Arial"/></font>
<font><i/><sz val="9"/><color rgb="FF6B7280"/><name val="Arial"/></font>
<font><sz val="10"/><color rgb="FF008000"/><name val="Arial"/></font>
<font><b/><sz val="16"/><color rgb="FF1F6F4A"/><name val="Arial"/></font>
</fonts>
<fills count="4">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF111827"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF3F4F6"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top style="thin"><color rgb="FF111827"/></top><bottom/><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="14">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="6" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>
<xf numFmtId="166" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="166" fontId="7" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def col_letter(idx: int) -> str:
    """1-based column index to letters."""
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Sheet:
    name: str
    rows: dict = field(default_factory=dict)       # row -> {col: (value, style)}
    widths: dict = field(default_factory=dict)
    freeze_row: int = 0
    autofilter: str | None = None

    def set(self, row: int, col: int, value, style: int = DEFAULT):
        self.rows.setdefault(row, {})[col] = (value, style)

    def write_row(self, row: int, values, style: int = DEFAULT, start_col: int = 1):
        for i, v in enumerate(values):
            if isinstance(v, tuple):
                self.set(row, start_col + i, v[0], v[1])
            else:
                self.set(row, start_col + i, v, style)

    def _xml(self) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
        if self.freeze_row:
            out.append(f'<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="{self.freeze_row}" '
                       f'topLeftCell="A{self.freeze_row + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
        else:
            out.append('<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>')
        if self.widths:
            out.append("<cols>" + "".join(
                f'<col min="{c}" max="{c}" width="{w}" customWidth="1"/>' for c, w in sorted(self.widths.items())) + "</cols>")
        out.append("<sheetData>")
        for r in sorted(self.rows):
            cells = []
            for c in sorted(self.rows[r]):
                v, s = self.rows[r][c]
                ref = f"{col_letter(c)}{r}"
                if v is None or v == "":
                    cells.append(f'<c r="{ref}" s="{s}"/>')
                elif isinstance(v, F):
                    cells.append(f'<c r="{ref}" s="{s}"><f>{escape(str(v))}</f></c>')
                elif isinstance(v, bool):
                    cells.append(f'<c r="{ref}" s="{s}" t="inlineStr"><is><t>{"Y" if v else "N"}</t></is></c>')
                elif isinstance(v, (int, float)):
                    cells.append(f'<c r="{ref}" s="{s}"><v>{repr(float(v)) if isinstance(v, float) else v}</v></c>')
                else:
                    t = escape(_CTRL.sub("", str(v)))
                    cells.append(f'<c r="{ref}" s="{s}" t="inlineStr"><is><t xml:space="preserve">{t}</t></is></c>')
            out.append(f'<row r="{r}">' + "".join(cells) + "</row>")
        out.append("</sheetData>")
        if self.autofilter:
            out.append(f'<autoFilter ref="{self.autofilter}"/>')
        out.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
        out.append("</worksheet>")
        return "".join(out)


class Workbook:
    def __init__(self):
        self.sheets: list[Sheet] = []

    def add(self, name: str) -> Sheet:
        if len(name) > 31 or re.search(r"[\[\]\*\?/\\:]", name):
            raise ValueError(f"invalid sheet name {name!r}")
        s = Sheet(name)
        self.sheets.append(s)
        return s

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        n = len(self.sheets)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                       '<Default Extension="xml" ContentType="application/xml"/>'
                       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                       + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                 for i in range(1, n + 1)) + "</Types>")
            z.writestr("_rels/.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                       "</Relationships>")
            z.writestr("xl/workbook.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                       + "".join(f'<sheet name="{escape(s.name)}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(self.sheets, 1))
                       + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                                 for i in range(1, n + 1))
                       + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                       "</Relationships>")
            z.writestr("xl/styles.xml", STYLES_XML)
            for i, s in enumerate(self.sheets, 1):
                z.writestr(f"xl/worksheets/sheet{i}.xml", s._xml())
        return buf.getvalue()
