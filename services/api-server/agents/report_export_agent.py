from __future__ import annotations

import hashlib
import json
import textwrap
import zipfile
from base64 import b64encode
from decimal import Decimal, ROUND_HALF_UP
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
    DISPLAY_NOTE = "표시값은 반올림한 정수입니다. 금리·비율은 %로 환산하며 반올림된 비율에는 ‘약’을 표시합니다. 정확한 값은 JSON 다운로드에서 확인해 주세요."
    ENGINE_INPUT_NOTE = "저장된 결과의 입력 요약에는 엔진 기본값이 포함될 수 있습니다. 확인된 원본 입력과 누락 여부는 ‘선택 계산의 입력 대조’ 및 JSON의 저장 입력에서 확인해 주세요."
    _PERCENT_LABELS = {
        "금리(소수)": "금리", "수수료율(소수)": "수수료율",
        "interestRate": "금리", "interest_rate": "금리", "feeRate": "수수료율", "fee_rate": "수수료율",
        "allInCost": "금리·수수료율 합계", "nominal_rate_plus_fee_proxy": "명목 금리·수수료율 합계",
        "targetRate": "목표 비율", "hurdleRate": "기준 수익률", "hurdle_rate": "기준 수익률",
        "salesValueLtv": "분양가 기준 LTV", "sales_value_ltv": "분양가 기준 LTV",
        "appraisal_ltv": "감정가 기준 LTV", "unsold_ltv": "미분양 담보 LTV", "unsoldLtv": "미분양 담보 LTV",
        "senior_ltv": "선순위 LTV", "total_ltv": "총 LTV", "ltv": "LTV", "ltc": "LTC",
        "bestCaseLtv": "최선 LTV", "baseCaseLtv": "기준 LTV", "stressCaseLtv": "스트레스 LTV", "maxLtv": "최대 LTV",
        "exitSalesRate": "Exit 분양률", "exitSaleRate": "Exit 분양률", "exit_sale_rate": "Exit 분양률",
        "projectIrr": "사업 IRR", "project_irr": "사업 IRR", "equityIrr": "자기자본 IRR", "equity_irr": "자기자본 IRR", "irr": "IRR",
        "profit_margin": "이익률", "profit_on_cost": "원가 대비 수익률", "cost_ratio": "원가 비율", "recoveryRatio": "회수율", "share": "지분율",
        "profitMargin": "이익률", "profitOnCost": "원가 대비 수익률", "breakEvenSaleRate": "손익분기 분양률",
        "saleRate": "분양률", "saleRates": "분양률", "unsoldRate": "미분양률", "unsoldRates": "미분양률",
        "discountRate": "할인율", "discountRates": "할인율", "ratio": "비율", "costRatio": "원가 구성비",
        "salesRate": "분양률", "collectionRate": "수금 비율", "interestRateAddition": "추가 금리",
        "contractAndInterimToRepayment": "계약·중도금 상환배분율", "contract_and_interim_to_repayment": "계약·중도금 상환배분율",
        "contractAndInterimToOperating": "계약·중도금 운영배분율", "contract_and_interim_to_operating": "계약·중도금 운영배분율",
        "allSalesProceedsToRepayment": "전체 수금 상환배분율", "all_sales_proceeds_to_repayment": "전체 수금 상환배분율",
        "balanceToRepayment": "잔금 상환배분율", "balance_to_repayment": "잔금 상환배분율",
        "minSalesRate": "최소 분양률", "min_sales_rate": "최소 분양률",
        "maxSalesRate": "최대 분양률", "max_sales_rate": "최대 분양률",
    }

    @staticmethod
    def whole_number_text(value: int | float | Decimal) -> str:
        """Round only the displayed decimal value; ties move away from zero."""
        number = Decimal(str(value))
        if not number.is_finite():
            return "미확인"
        rounded = number.to_integral_value(rounding=ROUND_HALF_UP)
        return "0" if rounded == 0 else format(rounded, ",f")

    @classmethod
    def presentation_value(cls, value: Any, field: str = "") -> Any:
        """A disposable human projection. Captured inputs and JSON stay exact."""
        if isinstance(value, dict):
            projected = {}
            for key, inner in value.items():
                if key == "periodBasis":
                    projected["기간 기준"] = cls.presentation_value(cls._period_basis_document(inner))
                    continue
                if (key == "summary" and isinstance(inner, dict)
                        and (value.get("periodBasis") or {}).get("kind") == "source_sequence"):
                    inner = dict(inner)
                    if "maxMinusMonth" in inner:
                        ordinal = inner.pop("maxMinusMonth")
                        # The calculator stores a one-based row ordinal here;
                        # sourcePeriodIndex and table rows are zero-based.
                        index = ordinal - 1 if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal > 0 else None
                        period = next((item for item in value["periodBasis"].get("periods") or []
                                       if item.get("sourcePeriodIndex") == index), None)
                        inner["최대자금부족 원본 기간"] = (
                            f"원본 {index + 1}: {period['label']}" if period is not None
                            else f"계산 순서 {index + 1} (원본 대응 미확인)" if index is not None
                            else "미확인")
                semantic = ("ratio" if field == "costRatio" and key in {"land", "construction", "finance", "other"}
                            else "collectionRate" if field == "collectionSteps" and key == "rate" else key)
                label = cls._PERCENT_LABELS.get(semantic, key) if semantic != "ratio" or key == "ratio" else key
                if label in projected:
                    label = f"{label} ({key})"
                projected[label] = cls.presentation_value(
                    inner, field if key == "value" and field in cls._PERCENT_LABELS else semantic)
            return projected
        if isinstance(value, list):
            return [cls.presentation_value(item, field) for item in value]
        if field == "금리 단위" and isinstance(value, str):
            return "금리·수수료율은 %로 환산한 뒤 정수로 반올림합니다."
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            # Identifiers can be numbers too. They are labels, never amounts.
            if str(field).lower().replace("_", "").endswith("id") or field in {"계산 기록", "보고서 번호", "계산 시각", "createdAt", "created_at"}:
                return str(value)
            if field in cls._PERCENT_LABELS:
                number = Decimal(str(value)) * 100
                if not number.is_finite():
                    return "미확인"
                rounded = number.to_integral_value(rounding=ROUND_HALF_UP)
                return ("약 " if rounded != number else "") + cls.whole_number_text(number) + "%"
            return cls.whole_number_text(value)
        return value

    @staticmethod
    def _period_basis_document(basis) -> dict:
        """Explain captured timing without printing technical metadata as dates."""
        if not isinstance(basis, dict) or basis.get("kind") not in {"source_sequence", "calendar_monthly"}:
            return {"상태": "미기록"}
        sequence = basis.get("kind") == "source_sequence"
        document = {"구분": "원본 자료 순서 — 경과 개월 미확인" if sequence else "월별 계산 기준",
                    "계산 행 수": basis.get("calculationPeriodCount")}
        periods = basis.get("periods") or []
        if sequence:
            document["원본 기간 행 수"] = len(periods)
            document["원본 월 번호가 확인된 행 수"] = sum(item.get("periodKind") == "monthly" for item in periods)
            document["해석"] = "원본 행 순서는 사건·월별 기간을 함께 포함합니다. 한 행을 한 달이나 달력 날짜로 바꾸지 않습니다. 전체 기간 목록은 JSON에 보관합니다."
            events = []
            for item in periods:
                if item.get("periodKind") == "monthly":
                    continue
                locator = item.get("sourceLocator") or {}
                events.append({"원본 순서": item.get("sourcePeriodIndex", 0) + 1,
                               "사건": item.get("label"), "실제 날짜·경과 개월": "미확인",
                               "원본": locator.get("sourceName") or locator.get("sourceDocument") or "미기록",
                               "위치": "!".join(str(value) for value in (locator.get("sheet") or locator.get("sheetName"), locator.get("cell")) if value) or "미기록"})
            document["기간 확인이 필요한 사건"] = events
            document["일정의 원본 대응"] = [
                {"일정": {"construction_start": "착공", "completion": "준공"}.get(key, key),
                 "원본 월 번호": value.get("sourceMonthNumber"),
                 "원본 순서": value["sourcePeriodIndex"] + 1 if value.get("sourcePeriodIndex") is not None else None}
                for key, value in (basis.get("scheduleBindings") or {}).items()]
        interest = basis.get("interestCalculation") or {}
        if interest.get("status") == "calculated":
            count = interest.get("chargedPeriodCount")
            document["월별 이자 계산"] = {"구분": "기초잔액 × 연금리 ÷ 12", "이자 발생 행 수": count,
                                         "최초 이자 월차": interest.get("firstChargedMonthIndex"),
                                         "마지막 이자 월차": interest.get("lastChargedMonthIndex")}
        else:
            document["월별 이자 계산"] = "재계산하지 않음 — 원본의 금융비용 예산을 사용하며 실제 이자기간은 미확인입니다."
        if basis.get("warnings"):
            document["확인할 사항"] = basis["warnings"]
        return document

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
            lines = [f"# {report_record.get('title') or report.get('title') or 'PF Review Report'}", "", self.DISPLAY_NOTE, ""]
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
        if report.get("calculationSnapshots"):
            rows = [(meta, {"표시 안내": ReportExportAgent.ENGINE_INPUT_NOTE, **value})
                    if meta.get("key") in {"businessFeasibility", "salesRevenueDistribution", "exitStrategy"}
                    and isinstance(value, dict) else (meta, value) for meta, value in rows]
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
  <p class="meta">{escape(self.DISPLAY_NOTE)}</p>
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
        return self._render_presented_value(self.presentation_value(value))

    def _render_presented_value(self, value: Any) -> str:
        if isinstance(value, dict):
            rows = "\n".join(
                f"<tr><th>{escape(str(key))}</th><td>{self._render_presented_value(inner)}</td></tr>"
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
                    "<tr>" + "".join(f"<td>{self._render_presented_value(item.get(key, ''))}</td>" for key in keys) + "</tr>"
                    for item in value
                )
                return f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
            items = "".join(f"<li>{self._render_presented_value(item)}</li>" for item in value)
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
            self.DISPLAY_NOTE,
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
        return self._flatten_presented_text(self.presentation_value(value), indent)

    def _flatten_presented_text(self, value: Any, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        if isinstance(value, dict):
            lines: list[str] = []
            for key, inner in value.items():
                if isinstance(inner, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.extend(self._flatten_presented_text(inner, indent + 1))
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
                    lines.extend(self._flatten_presented_text(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {self._scalar_text(item)}")
            return lines
        return [f"{prefix}{self._scalar_text(value)}"]

    @classmethod
    def _scalar_text(cls, value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return cls.whole_number_text(value)
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
                          (f"버전: {report_record.get('version', '')}", "Normal"),
                          (self.DISPLAY_NOTE, "Normal"), ("목차", "Heading1")]
            paragraphs.extend((f"{meta.get('index', '')}. {meta.get('title', '')}", "Normal") for meta, _ in sections)
            blocks = [self._docx_paragraph(line, style) for line, style in paragraphs]
            blocks.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            for meta, value in sections:
                blocks.append(self._docx_paragraph(f"{meta.get('index', '')}. {meta.get('title', '')}", "Heading1"))
                if meta.get("key") == "calculationReconciliation":
                    blocks.append(self._docx_reconciliation(report["calculationReconciliation"]))
                elif meta.get("key") == "podEvidence":
                    blocks.append(self._docx_empty_pod_evidence(value))
                elif report.get("financingBasis") and meta.get("key") in {"financialTerms", "fundingStructure", "useOfFunds"}:
                    blocks.append(self._docx_financing(value))
                elif meta.get("key") == "summaryCashFlow":
                    blocks.append(self._docx_summary_cash_flow(value))
                elif report.get("timingBasis") and meta.get("key") == "salesPaymentSchedule":
                    blocks.append(self._docx_sales_collections(value))
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

    def _docx_empty_pod_evidence(self, evidence) -> str:
        """Summarize empty linkage metadata; keep every substantive record intact."""
        links = evidence.get("links") if isinstance(evidence, dict) else None
        detail_keys = ("linkedRecords", "podAiReviewFindings", "evidenceRecordIds", "metricRecordIds",
                       "riskIssueRecordIds", "assumptionRecordIds", "linkedRecordCount", "podAiReviewBlocking")
        if not (isinstance(evidence, dict) and evidence.get("podReviewReadStatus") == "loaded"
                and isinstance(links, list) and links and all(isinstance(link, dict)
                and not any(link.get(key) for key in detail_keys)
                and not any((link.get("sourceLocatorCoverage") or {}).values()) for link in links)):
            return '\n'.join(self._docx_paragraph(line) for line in self._flatten_text(evidence))
        blocks = [self._docx_paragraph("각 절에 등록된 근거·Pod 지적이 없습니다. 지적 없음은 자료 확인이나 승인을 뜻하지 않습니다. 전체 연결 메타데이터는 JSON에 보관합니다.")]
        status = evidence.get("podReviewReadStatus") or "미기록"
        blocks.append(self._docx_paragraph("Pod 검토 조회: " + ("조회 완료" if status == "loaded" else str(status))))
        if evidence.get("podReviewReadMessage"):
            blocks.append(self._docx_paragraph(str(evidence["podReviewReadMessage"])))
        rows = [("보고서 절", "필요한 자료", "등록된 근거·원본 위치·지적")]
        for link in links:
            required = ' · '.join(str(item) for item in link.get("requiredEvidence") or []) or "미기록"
            rows.append((str(link.get("title") or link.get("sectionKey") or "미기록"), required, "없음 · 미확인"))
        blocks.append(self._docx_table(rows, (2700, 3300, 3638), set()))
        return '\n'.join(blocks)

    def _docx_reconciliation(self, reconciliation: dict) -> str:
        """Keep each numeric comparison on one table row, including its status."""
        from use_cases.report_input_reconciliation import STATUS_LABELS

        blocks = [self._docx_paragraph(reconciliation[key]) for key in ("summary", "limitation")]
        runs = {cell["slot"]: cell for row in reconciliation["rows"] for cell in row["values"]}
        for cell in runs.values():
            basis = "저장 입력 단위" if cell["unitSource"] == "captured" else "계산 API 기본 원 단위"
            blocks.append(self._docx_paragraph(f"{cell['label']} · 계산 기록 {cell['calculationRunId']} · {basis}"))
        rows = [("항목", "계산", "저장 입력과 단위", "대조 상태")]
        for row in reconciliation["rows"]:
            for cell in row["values"]:
                value = self.whole_number_text(cell["value"]) if cell["status"] == "present" else "미기록"
                rows.append((row["label"], cell["label"], f"{value} {cell['currency']} / {cell['unit']}", STATUS_LABELS[row["status"]]))
        blocks.append(self._docx_table(rows, (2050, 1900, 3700, 1988), {2}) if len(rows) > 1
                      else self._docx_paragraph("대조 가능한 공통 예산 입력이 없습니다."))
        if reconciliation["sameFamilyChecks"]:
            blocks.append(self._docx_paragraph("같은 계산 계열의 전체 업무입력", "Heading1"))
            for pair in reconciliation["sameFamilyChecks"]:
                blocks.append(self._docx_paragraph(f"{pair['label']} · {STATUS_LABELS[pair['status']]} · {pair['leftRunId']} / {pair['rightRunId']}"))
        return '\n'.join(blocks)

    def _docx_financing(self, section: dict) -> str:
        list_keys = {"조달 입력", "사업 수입 입력", "사용 예산 입력", "트랜치 조건"}
        blocks = [self._docx_paragraph(line) for line in self._flatten_text(
            {key: value for key, value in section.items() if key not in list_keys})]
        def amount(value):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return "미확인"
            return self.whole_number_text(value)
        for key, items in section.items():
            if key not in list_keys or not items:
                continue
            blocks.append(self._docx_paragraph(key, "Heading1"))
            if key == "트랜치 조건":
                rows = [("명칭", "금액", "금리", "수수료율", "상환순위")]
                rows.extend((str(item["명칭"]), amount(item["금액"]),
                    self._scalar_text(self.presentation_value(item["금리(소수)"], "금리(소수)")),
                    self._scalar_text(self.presentation_value(item["수수료율(소수)"], "수수료율(소수)")),
                    self._scalar_text(item["상환순위 입력"])) for item in items)
                blocks.append(self._docx_table(rows, (1950, 2650, 1600, 1600, 1838), {1, 2, 3}))
            else:
                rows = [("항목", "금액")]
                rows.extend((item["항목"], amount(item["금액"])) for item in items)
                blocks.append(self._docx_table(rows, (4838, 4800), {1}))
        return '\n'.join(blocks)

    def _docx_summary_cash_flow(self, section: dict) -> str:
        rows = section.get("rows") if isinstance(section, dict) else None
        if not (isinstance(rows, list) and rows and all(isinstance(row, dict) and "monthIndex" in row for row in rows)):
            return '\n'.join(self._docx_paragraph(line) for line in self._flatten_text(section))
        blocks = [self._docx_paragraph(line) for line in self._flatten_text(
            {key: value for key, value in section.items() if key != "rows"})]
        sequence = (section.get("periodBasis") or {}).get("kind") == "source_sequence"
        blocks.append(self._docx_paragraph(("표는 원본 자료의 순서이며 각 행이 한 달을 뜻하지 않습니다. " if sequence else "월별 표는 저장된 월차를 사용합니다. ")
            + "수금 구분·PF 인출·지출·순현금·기말잔액은 저장 결과입니다. 합계나 누락값을 새로 계산하지 않습니다. 세부 계정과 원본 값은 JSON에서 확인해 주세요."))
        table = [("원본 기간" if sequence else "월차 / 기간", "분양수금", "PF 인출", "총 지출", "순현금", "기말잔액")]
        for row in rows:
            inflow = row.get("cashIn") if isinstance(row.get("cashIn"), dict) else {}
            outflow = row.get("cashOut") if isinstance(row.get("cashOut"), dict) else {}
            sales = inflow.get("salesCollection")
            if isinstance(sales, dict):
                collection = '\n'.join(f"{label} {self._saved_amount(sales.get(key))}"
                    for key, label in (("contract", "계약"), ("interim", "중도"), ("balance", "잔금")))
            else:
                collection = self._saved_amount(sales)
            month = f"M{row['monthIndex']}"
            period = row.get("period")
            label = str(period).replace(": ", "\n", 1) if sequence and period is not None else month if period == month else f"{month}\n{period if period is not None else '—'}"
            table.append((label, collection, self._saved_amount(inflow.get("totalPfDraw")),
                self._saved_amount(outflow.get("totalCashOut")), self._saved_amount(row.get("netCashFlow")),
                self._saved_amount(row.get("endingCash"))))
        blocks.append(self._docx_table(table, (1000, 1638, 1600, 1800, 1800, 1800), {1, 2, 3, 4, 5}, font_size=18, cell_padding=80))
        return '\n'.join(blocks)

    def _docx_sales_collections(self, section: dict) -> str:
        collections = section.get("저장된 수금 결과") if isinstance(section, dict) else None
        if not (isinstance(collections, list) and collections and all(isinstance(row, dict) for row in collections)):
            return '\n'.join(self._docx_paragraph(line) for line in self._flatten_text(section))
        blocks = [self._docx_paragraph(line) for line in self._flatten_text(
            {key: value for key, value in section.items() if key not in {"저장된 수금 결과", "수금 입력"}})]
        blocks.append(self._docx_paragraph("계약금·중도금·잔금은 저장된 수금 결과의 구분입니다. 상품별 수금 입력과 원본 셀의 전체 내용은 JSON에서 확인해 주세요."))
        basis = section.get("periodBasis") or {}
        sequence = basis.get("kind") == "source_sequence"
        labels = {item["sourcePeriodIndex"]: item["label"] for item in basis.get("periods") or []}
        rows = [("원본 기간" if sequence else "월차", "계약금", "중도금", "잔금")]
        rows.extend(((f"원본 {index + 1}\n{labels[index]}" if index in labels else f"계산 순서 {index + 1} (원본 미기록)") if sequence else f"M{index}", *(self._saved_amount(row.get(key)) for key in ("contract", "interim", "balance")))
                    for index, row in enumerate(collections))
        blocks.append(self._docx_table(rows, (1038, 2866, 2867, 2867), {1, 2, 3}))
        return '\n'.join(blocks)

    @classmethod
    def _saved_amount(cls, value) -> str:
        return cls.whole_number_text(value) if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) else "—"

    def _docx_table(self, rows, widths, right_columns, *, font_size: int = 20, cell_padding: int = 100) -> str:
        """Repeat headers and keep each record together across page breaks."""
        borders = "".join(f'<w:{edge} w:val="single" w:sz="4" w:color="D9D9D9"/>' for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
        table = [f'<w:tbl><w:tblPr><w:tblW w:w="9638" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>{borders}</w:tblBorders><w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:left w:w="{cell_padding}" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/><w:right w:w="{cell_padding}" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>']
        table.extend(f'<w:gridCol w:w="{width}"/>' for width in widths)
        table.append('</w:tblGrid>')
        for index, row in enumerate(rows):
            table.append('<w:tr><w:trPr><w:cantSplit/>' + ('<w:tblHeader/>' if index == 0 else '') + '</w:trPr>')
            for column, (width, text) in enumerate(zip(widths, row)):
                shade = '<w:shd w:fill="DCE6F1"/>' if index == 0 else ''
                alignment = "center" if index == 0 else "right" if column in right_columns else "left"
                paragraph = self._docx_paragraph(text).replace('<w:pPr>', f'<w:pPr><w:jc w:val="{alignment}"/>')
                paragraph = paragraph.replace('<w:r>', f'<w:r><w:rPr><w:sz w:val="{font_size}"/></w:rPr>')
                if index == 0:
                    paragraph = paragraph.replace('<w:pPr>', '<w:pPr><w:keepNext/>')
                table.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/><w:vAlign w:val="center"/>{shade}</w:tcPr>{paragraph}</w:tc>')
            table.append('</w:tr>')
        table.append('</w:tbl>')
        return ''.join(table)

    @staticmethod
    def _docx_paragraph(text: str, style: str = "Normal") -> str:
        safe_text = xml_escape(text).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">')
        return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{safe_text}</w:t></w:r></w:p>'

    def _file_name(self, report_record: Dict[str, Any], extension: str) -> str:
        report_id = str(report_record.get("report_id", "report"))[:8]
        version = report_record.get("version", 1)
        return f"phase11-pf-review-{report_id}-v{version}.{extension}"
