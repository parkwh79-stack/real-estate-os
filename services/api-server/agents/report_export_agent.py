from __future__ import annotations

import hashlib
import json
import textwrap
import zipfile
from base64 import b64encode
from io import BytesIO
from html import escape
from typing import Any, Dict
from xml.sax.saxutils import escape as xml_escape


class ReportExportAgent:
    supported_formats = {"markdown", "json", "html", "pdf", "docx"}
    _PDF_PAGE_WIDTH = 595
    _PDF_HORIZONTAL_MARGIN = 42
    _PDF_FONT_SIZE = 10
    _PDF_CID_WIDTH = 1000

    def export(self, report_record: Dict[str, Any], export_format: str) -> Dict[str, Any]:
        normalized_format = export_format.lower().strip()
        if normalized_format not in self.supported_formats:
            raise ValueError(f"Unsupported report export format: {export_format}")

        if normalized_format == "markdown":
            content = self._render_markdown(report_record)
            content_type = "text/markdown; charset=utf-8"
            extension = "md"
        elif normalized_format == "json":
            content = self._render_json(report_record)
            content_type = "application/json; charset=utf-8"
            extension = "json"
        elif normalized_format == "html":
            content = self._render_html(report_record)
            content_type = "text/html; charset=utf-8"
            extension = "html"
        elif normalized_format == "pdf":
            content_bytes = self._render_pdf(report_record)
            return self._binary_export(report_record, normalized_format, "pdf", "application/pdf", content_bytes)
        else:
            content_bytes = self._render_docx(report_record)
            return self._binary_export(
                report_record,
                normalized_format,
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                content_bytes,
            )

        encoded = content.encode("utf-8")
        return {
            "format": normalized_format,
            "file_name": self._file_name(report_record, extension),
            "content_type": content_type,
            "content": content,
            "byte_size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def _render_markdown(self, report_record: Dict[str, Any]) -> str:
        report = report_record.get("report") or {}
        if report.get("reportType") == "cash-flow-comparison-review" and report_record.get("report_markdown"):
            # Comparison drafts already seal their narrative together with the
            # two calculations. Preserve that stored download byte-for-byte.
            return report_record["report_markdown"]
        if report.get("sectionOrder"):
            lines = [f"# {report_record.get('title') or report.get('title') or 'PF Review Report'}", ""]
            for meta, value in self._report_sections(report):
                lines.extend([f"## {meta.get('index', '')}. {meta.get('title', '')}", ""])
                lines.extend(self._flatten_text(value))
                lines.append("")
            return "\n".join(lines)
        return report_record.get("report_markdown") or f"# {report_record.get('title', 'PF Review Report')}\n"

    @staticmethod
    def _report_sections(report: Dict[str, Any]) -> list[tuple[dict, Any]]:
        """All formats consume the saved body and saved provenance, never live reviews."""
        rows = [(meta, (report.get("sections") or {}).get(meta.get("key")))
                for meta in report.get("sectionOrder") or []]
        reconciliation = report.get("calculationReconciliation")
        if reconciliation:
            from use_cases.report_input_reconciliation import reconciliation_document
            rows.append(({"index": "부록", "key": "calculationReconciliation", "title": "선택 계산의 입력 대조"},
                         reconciliation_document(reconciliation)))
        links = report.get("paragraphEvidenceLinks") or []
        if links:
            rows.append(({"index": "부록", "key": "podEvidence", "title": "문단별 근거와 Pod 보완사항"}, links))
        sources = report.get("calculationSources")
        if sources:
            rows.append(({"index": "부록", "key": "calculationSources", "title": "저장된 계산과 입력 기준"}, sources))
        if report.get("templateApplication"):
            rows.append(({"index": "부록", "key": "templateApplication", "title": "적용 보고서 템플릿"}, report["templateApplication"]))
        return rows

    def _render_json(self, report_record: Dict[str, Any]) -> str:
        payload = {
            "report_id": report_record.get("report_id"),
            "project_id": report_record.get("project_id"),
            "run_id": report_record.get("run_id"),
            "version": report_record.get("version"),
            "template": report_record.get("template"),
            "title": report_record.get("title"),
            "status": report_record.get("status"),
            "created_at": report_record.get("created_at"),
            "metadata": report_record.get("metadata", {}),
            "report": report_record.get("report", {}),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _render_html(self, report_record: Dict[str, Any]) -> str:
        report = report_record.get("report", {})
        title = report_record.get("title") or report.get("title") or "PF Review Report"
        report_sections = self._report_sections(report)
        toc_items = "\n".join(
            f'<li><a href="#{escape(str(item.get("key", "")))}">{escape(str(item.get("index", "")))}. {escape(str(item.get("title", "")))}</a></li>'
            for item, _ in report_sections
        )
        body = "\n".join(
            self._render_section(item, value)
            for item, value in report_sections
        )

        return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>{escape(str(title))}</title>
  <style>
    @page {{ size: A4; margin: 16mm; }}
    body {{ font-family: Arial, 'Malgun Gothic', sans-serif; color: #111827; line-height: 1.5; }}
    h1 {{ font-size: 24px; margin-bottom: 4px; }}
    h2 {{ border-bottom: 1px solid #d1d5db; font-size: 18px; margin-top: 28px; padding-bottom: 6px; }}
    table {{ border-collapse: collapse; table-layout: fixed; width: 100%; margin: 8px 0 16px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: top; word-break: break-word; }}
    th {{ background: #f3f4f6; text-align: left; }}
    pre {{ background: #f9fafb; border: 1px solid #e5e7eb; padding: 8px; white-space: pre-wrap; word-break: break-word; }}
    .meta {{ color: #6b7280; font-size: 12px; }}
    .toc {{ break-after: page; }}
    .section {{ break-inside: avoid; }}
  </style>
</head>
<body>
  <h1>{escape(str(title))}</h1>
  <p class="meta">Report ID: {escape(str(report_record.get("report_id", "")))} | Version: {escape(str(report_record.get("version", "")))}</p>
  <section class="toc">
    <h2>Table of Contents</h2>
    <ol>{toc_items}</ol>
  </section>
  {body}
</body>
</html>"""

    def _render_section(self, section_meta: Dict[str, Any], section_value: Any) -> str:
        key = str(section_meta.get("key", "section"))
        title = f'{section_meta.get("index", "")}. {section_meta.get("title", key)}'
        return f"""
<section id="{escape(key)}" class="section">
  <h2>{escape(str(title))}</h2>
  {self._render_value(section_value)}
</section>"""

    def _render_value(self, value: Any) -> str:
        if isinstance(value, dict):
            rows = "\n".join(
                f"<tr><th>{escape(str(key))}</th><td>{self._render_value(inner)}</td></tr>"
                for key, inner in value.items()
            )
            return f"<table>{rows}</table>"
        if isinstance(value, list):
            if not value:
                return "<p>-</p>"
            if all(isinstance(item, dict) for item in value):
                keys = []
                for item in value:
                    for key in item.keys():
                        if key not in keys:
                            keys.append(key)
                header = "".join(f"<th>{escape(str(key))}</th>" for key in keys)
                rows = "\n".join(
                    "<tr>" + "".join(f"<td>{self._render_value(item.get(key, ''))}</td>" for key in keys) + "</tr>"
                    for item in value
                )
                return f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
            items = "".join(f"<li>{self._render_value(item)}</li>" for item in value)
            return f"<ul>{items}</ul>"
        return escape(str(value if value is not None else "-"))

    def _binary_export(
        self,
        report_record: Dict[str, Any],
        export_format: str,
        extension: str,
        content_type: str,
        content_bytes: bytes,
    ) -> Dict[str, Any]:
        return {
            "format": export_format,
            "file_name": self._file_name(report_record, extension),
            "content_type": content_type,
            "content": b64encode(content_bytes).decode("ascii"),
            "content_encoding": "base64",
            "byte_size": len(content_bytes),
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
        }

    def _plain_report_lines(self, report_record: Dict[str, Any]) -> list[str]:
        report = report_record.get("report", {})
        title = str(report_record.get("title") or report.get("title") or "PF Review Report")
        lines = [
            title,
            f"Report ID: {report_record.get('report_id', '')}",
            f"Version: {report_record.get('version', '')}",
            "",
        ]
        report_sections = self._report_sections(report)
        if report_sections:
            lines.append("Table of Contents")
            for item, _ in report_sections:
                lines.append(f"{item.get('index', '')}. {item.get('title', item.get('key', ''))}")
            lines.append("")

        for item, value in report_sections:
            key = item.get("key")
            lines.append(f"{item.get('index', '')}. {item.get('title', key)}")
            lines.extend(self._flatten_text(value, indent=0))
            lines.append("")

        if not report_sections:
            markdown = self._render_markdown(report_record)
            lines.extend(markdown.splitlines())
        return lines

    def _flatten_text(self, value: Any, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        if isinstance(value, dict):
            lines: list[str] = []
            for key, inner in value.items():
                if isinstance(inner, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.extend(self._flatten_text(inner, indent + 1))
                else:
                    lines.append(f"{prefix}{key}: {self._scalar_text(inner)}")
            return lines or [f"{prefix}-"]
        if isinstance(value, list):
            if not value:
                return [f"{prefix}-"]
            lines = []
            for item in value:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.extend(self._flatten_text(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {self._scalar_text(item)}")
            return lines
        return [f"{prefix}{self._scalar_text(value)}"]

    @staticmethod
    def _scalar_text(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.4f}".rstrip("0").rstrip(".")
        return str(value)

    def _render_pdf(self, report_record: Dict[str, Any]) -> bytes:
        lines = self._plain_report_lines(report_record)
        # The CID font declares /DW only: each UTF-16 code unit advances a full
        # em, including Latin letters and spaces. Character-count width=86
        # overflowed A4; derive the line budget from the emitted PDF geometry.
        line_units = int(
            (self._PDF_PAGE_WIDTH - 2 * self._PDF_HORIZONTAL_MARGIN)
            / (self._PDF_FONT_SIZE * self._PDF_CID_WIDTH / 1000)
        )
        wrapped_lines: list[str] = []
        for line in lines:
            for paragraph in line.splitlines() or [""]:
                for wrapped in textwrap.wrap(paragraph, width=line_units, replace_whitespace=False) or [""]:
                    # A non-BMP character occupies two UTF-16 units. Preserve
                    # its surrogate pair in one text operator when wrapping.
                    chunk = ""
                    used_units = 0
                    for character in wrapped:
                        units = len(character.encode("utf-16-be")) // 2
                        if used_units + units > line_units:
                            wrapped_lines.append(chunk)
                            chunk = ""
                            used_units = 0
                        chunk += character
                        used_units += units
                    wrapped_lines.append(chunk)

        page_line_count = 50
        headings = {f"{meta.get('index', '')}. {meta.get('title', '')}"
                    for meta, _ in self._report_sections(report_record.get("report") or {})}
        pages: list[list[str]] = []
        page: list[str] = []
        for line in wrapped_lines:
            if len(page) >= page_line_count or (line in headings and len(page) >= page_line_count - 2):
                pages.append(page)
                page = []
            page.append(line)
        if page or not pages:
            pages.append(page or [""])

        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /HYGothic-Medium /Encoding /UniKS-UCS2-H /DescendantFonts [4 0 R] >>",
            f"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HYGothic-Medium /FontDescriptor 5 0 R /CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) /Supplement 2 >> /DW {self._PDF_CID_WIDTH} >>".encode("ascii"),
            b"<< /Type /FontDescriptor /FontName /HYGothic-Medium /Flags 6 /FontBBox [-6 -145 1003 880] /ItalicAngle 0 /Ascent 752 /Descent -142 /CapHeight 737 /StemV 58 >>",
        ]
        page_refs = []
        for page_lines in pages:
            content = self._pdf_page_stream(page_lines)
            content_object_id = len(objects) + 1
            objects.append(
                b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream"
            )
            page_object_id = len(objects) + 1
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self._PDF_PAGE_WIDTH} 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_object_id} 0 R >>".encode("ascii")
            )
            page_refs.append(f"{page_object_id} 0 R")

        objects[1] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>".encode("ascii")

        output = BytesIO()
        output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(output.tell())
            output.write(f"{index} 0 obj\n".encode("ascii"))
            output.write(obj)
            output.write(b"\nendobj\n")
        xref_offset = output.tell()
        output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        output.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.write(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
        return output.getvalue()

    @classmethod
    def _pdf_page_stream(cls, lines: list[str]) -> bytes:
        commands = ["BT", f"/F1 {cls._PDF_FONT_SIZE} Tf", "14 TL"]
        y = 802
        for line in lines:
            encoded = line.encode("utf-16-be").hex().upper()
            commands.append(f"1 0 0 1 {cls._PDF_HORIZONTAL_MARGIN} {y} Tm <{encoded}> Tj")
            y -= 14
        commands.append("ET")
        return "\n".join(commands).encode("ascii")

    def _render_docx(self, report_record: Dict[str, Any]) -> bytes:
        report = report_record.get("report") or {}
        sections = self._report_sections(report)
        if sections:
            paragraphs = [(str(report_record.get("title") or report.get("title") or "PF 검토 보고서"), "Title"),
                          (f"보고서 번호: {report_record.get('report_id', '')}", "Normal"),
                          (f"버전: {report_record.get('version', '')}", "Normal"), ("목차", "Heading1")]
            paragraphs.extend((f"{meta.get('index', '')}. {meta.get('title', '')}", "Normal") for meta, _ in sections)
            blocks = [self._docx_paragraph(line, style) for line, style in paragraphs]
            for meta, value in sections:
                blocks.append(self._docx_paragraph(f"{meta.get('index', '')}. {meta.get('title', '')}", "Heading1"))
                if meta.get("key") == "calculationReconciliation":
                    blocks.append(self._docx_reconciliation(report["calculationReconciliation"]))
                else:
                    blocks.extend(self._docx_paragraph(line) for line in self._flatten_text(value))
        else:
            paragraphs = [(line, "Title" if index == 0 else "Normal")
                          for index, line in enumerate(self._plain_report_lines(report_record))]
            blocks = [self._docx_paragraph(line, style) for line, style in paragraphs]
        document_body = "\n".join(blocks)
        document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {document_body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>"""
        content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
        rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
        styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="100"/><w:wordWrap w:val="1"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Malgun Gothic" w:hAnsi="Malgun Gothic" w:eastAsia="Malgun Gothic"/><w:sz w:val="20"/><w:lang w:val="ko-KR" w:eastAsia="ko-KR"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:after="240"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="26"/></w:rPr>
  </w:style>
</w:styles>"""

        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as docx:
            docx.writestr("[Content_Types].xml", content_types)
            docx.writestr("_rels/.rels", rels)
            docx.writestr("word/document.xml", document_xml)
            docx.writestr("word/styles.xml", styles)
            docx.writestr("word/_rels/document.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''')
        return output.getvalue()

    def _docx_reconciliation(self, reconciliation: dict) -> str:
        """Keep each numeric comparison on one table row, including its status."""
        from use_cases.report_input_reconciliation import STATUS_LABELS

        blocks = [self._docx_paragraph(reconciliation[key]) for key in ("summary", "limitation")]
        runs = {cell["slot"]: cell for row in reconciliation["rows"] for cell in row["values"]}
        for cell in runs.values():
            basis = "저장 입력 단위" if cell["unitSource"] == "captured" else "계산 API 기본 원 단위"
            blocks.append(self._docx_paragraph(f"{cell['label']} · 계산 기록 {cell['calculationRunId']} · {basis}"))
        widths = (2050, 1900, 3700, 1988)
        rows = [("항목", "계산", "저장 입력과 단위", "대조 상태")]
        for row in reconciliation["rows"]:
            for cell in row["values"]:
                value = format(cell["value"], ",") if cell["status"] == "present" else "미기록"
                rows.append((row["label"], cell["label"], f"{value} {cell['currency']} / {cell['unit']}", STATUS_LABELS[row["status"]]))
        borders = "".join(f'<w:{edge} w:val="single" w:sz="4" w:color="D9D9D9"/>' for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
        table = [f'<w:tbl><w:tblPr><w:tblW w:w="9638" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>{borders}</w:tblBorders><w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>']
        table.extend(f'<w:gridCol w:w="{width}"/>' for width in widths)
        table.append('</w:tblGrid>')
        for index, row in enumerate(rows):
            table.append('<w:tr><w:trPr><w:cantSplit/>' + ('<w:tblHeader/>' if index == 0 else '') + '</w:trPr>')
            for column, (width, text) in enumerate(zip(widths, row)):
                shade = '<w:shd w:fill="DCE6F1"/>' if index == 0 else ''
                alignment = "center" if index == 0 else "right" if column == 2 else "left"
                paragraph = self._docx_paragraph(text).replace('<w:pPr>', f'<w:pPr><w:jc w:val="{alignment}"/>')
                table.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/><w:vAlign w:val="center"/>{shade}</w:tcPr>{paragraph}</w:tc>')
            table.append('</w:tr>')
        table.append('</w:tbl>')
        blocks.append(''.join(table) if len(rows) > 1 else self._docx_paragraph("대조 가능한 공통 예산 입력이 없습니다."))
        if reconciliation["sameFamilyChecks"]:
            blocks.append(self._docx_paragraph("같은 계산 계열의 전체 업무입력", "Heading1"))
            for pair in reconciliation["sameFamilyChecks"]:
                blocks.append(self._docx_paragraph(f"{pair['label']} · {STATUS_LABELS[pair['status']]} · {pair['leftRunId']} / {pair['rightRunId']}"))
        return '\n'.join(blocks)

    @staticmethod
    def _docx_paragraph(text: str, style: str = "Normal") -> str:
        safe_text = xml_escape(text)
        return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{safe_text}</w:t></w:r></w:p>'

    def _file_name(self, report_record: Dict[str, Any], extension: str) -> str:
        report_id = str(report_record.get("report_id", "report"))[:8]
        version = report_record.get("version", 1)
        return f"phase11-pf-review-{report_id}-v{version}.{extension}"
