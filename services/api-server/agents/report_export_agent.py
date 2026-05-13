from __future__ import annotations

import hashlib
import json
from html import escape
from typing import Any, Dict


class ReportExportAgent:
    supported_formats = {"markdown", "json", "html"}

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
        else:
            content = self._render_html(report_record)
            content_type = "text/html; charset=utf-8"
            extension = "html"

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
        return report_record.get("report_markdown") or f"# {report_record.get('title', 'PF Review Report')}\n"

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
        section_order = report.get("sectionOrder", [])
        sections = report.get("sections", {})
        toc_items = "\n".join(
            f'<li><a href="#{escape(str(item.get("key", "")))}">{escape(str(item.get("index", "")))}. {escape(str(item.get("title", "")))}</a></li>'
            for item in section_order
        )
        body = "\n".join(
            self._render_section(item, sections.get(item.get("key")))
            for item in section_order
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

    def _file_name(self, report_record: Dict[str, Any], extension: str) -> str:
        report_id = str(report_record.get("report_id", "report"))[:8]
        version = report_record.get("version", 1)
        return f"phase11-pf-review-{report_id}-v{version}.{extension}"
