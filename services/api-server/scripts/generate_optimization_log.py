"""Generate / update docs/ops/optimization-log.md from user-activity logs.

Part of the safe, human-in-the-loop recursive-optimization loop. Run on a
schedule or on demand:

    python scripts/generate_optimization_log.py [--limit 2000]

It reads structured user_activity_logs (metadata only — no PF payloads),
distils optimization signals, and PREPENDS a dated section to the rolling
docs/ops/optimization-log.md so history is preserved. A human reviews the
report and promotes items into docs/backlog.md; code changes still go through
normal review and tests.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

# Allow running as `python scripts/generate_optimization_log.py` from api-server.
API_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(API_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(API_SERVER_DIR))

from storage import list_user_activity_logs  # noqa: E402
from use_cases.usage_insights import build_usage_insights, render_optimization_log_markdown  # noqa: E402

REPO_ROOT = API_SERVER_DIR.parents[1]
LOG_PATH = REPO_ROOT / "docs" / "ops" / "optimization-log.md"

HEADER = """# Optimization Log (usage-driven)

Auto-generated from `user_activity_logs` by `services/api-server/scripts/generate_optimization_log.py`.
Newest report first. Metadata only — no sensitive PF data. See
`docs/product/recursive-optimization-loop.md` for the loop design. Maintainers
review each report and promote items into `docs/backlog.md`.

"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the usage-driven optimization log section.")
    parser.add_argument("--limit", type=int, default=2000, help="Max activity logs to analyze.")
    args = parser.parse_args()

    logs = list_user_activity_logs(limit=args.limit)
    insights = build_usage_insights(logs)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    section = render_optimization_log_markdown(insights, generated_at)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists():
        existing = LOG_PATH.read_text(encoding="utf-8")
        body = existing[len(HEADER):] if existing.startswith(HEADER) else existing
        LOG_PATH.write_text(HEADER + section + "\n" + body, encoding="utf-8")
    else:
        LOG_PATH.write_text(HEADER + section + "\n", encoding="utf-8")

    print(f"Wrote optimization report ({insights['totals']['events']} events) to {LOG_PATH}")
    for rec in insights["recommendations"]:
        print(f"  [{rec['severity']}] {rec['signal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
