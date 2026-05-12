from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_report_print_css_contains_a4_table_and_approval_guards():
    css = (ROOT / "apps" / "web" / "app" / "globals.css").read_text(encoding="utf-8")
    report_page = (ROOT / "apps" / "web" / "app" / "report" / "page.tsx").read_text(encoding="utf-8")

    assert "@page" in css
    assert "size: A4" in css
    assert ".report-print-page" in css
    assert ".report-table thead" in css
    assert "display: table-header-group" in css
    assert ".approval-block" in css
    assert "report-print-page" in report_page
    assert "approval-block" in report_page
