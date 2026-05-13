from pathlib import Path


def repo_root() -> Path:
    for path in Path(__file__).resolve().parents:
        if (path / "apps" / "web" / "app" / "globals.css").exists():
            return path
    fallback = Path.cwd()
    for path in [fallback, *fallback.parents]:
        if (path / "apps" / "web" / "app" / "globals.css").exists():
            return path
    raise FileNotFoundError("Could not locate repository root from test path or cwd.")


def test_report_print_css_contains_a4_table_and_approval_guards():
    root = repo_root()
    css = (root / "apps" / "web" / "app" / "globals.css").read_text(encoding="utf-8")
    report_page = (root / "apps" / "web" / "app" / "report" / "page.tsx").read_text(encoding="utf-8")

    assert "@page" in css
    assert "size: A4" in css
    assert ".report-print-page" in css
    assert ".report-table thead" in css
    assert "display: table-header-group" in css
    assert ".approval-block" in css
    assert "report-print-page" in report_page
    assert "approval-block" in report_page
