"""Command-line access to the hosted REALYTICS land and legal preview."""

from __future__ import annotations

import argparse
import json
import sys

from hosted_project_api import HostedProjectApi, HostedProjectApiError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="운영 프로젝트의 토지·법률 검토를 조회하고 선택 항목을 보관합니다.")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="공식 근거와 필지별 검토 상태를 조회합니다.")
    preview.add_argument("project_id")
    preview_range = preview.add_mutually_exclusive_group()
    preview_range.add_argument("--offset", type=int, dest="parcel_offset")
    preview_range.add_argument("--parcel-index", type=int, action="append", dest="parcel_indexes")
    preview.add_argument("--parcel-revision")

    save = commands.add_parser("save-summary", help="선택한 미리보기 항목을 프로젝트에 저장합니다.")
    save.add_argument("project_id")
    save.add_argument("--section", choices=("permit", "height", "parcels"), action="append", required=True, dest="sections")
    save.add_argument("--preview-hash", required=True, dest="expected_preview_hash")
    save.add_argument("--parcel-index", type=int, action="append", dest="parcel_indexes")
    saved_range = save.add_mutually_exclusive_group()
    saved_range.add_argument("--offset", type=int, dest="parcel_offset")
    saved_range.add_argument("--reviewed-parcel-index", type=int, action="append", dest="reviewed_parcel_indexes")
    save.add_argument("--parcel-revision")

    summaries = commands.add_parser("list-summaries", help="보관한 선택 요약을 조회합니다.")
    summaries.add_argument("project_id")
    arguments = vars(parser.parse_args(argv))
    command = arguments.pop("command")
    try:
        api = HostedProjectApi.from_environment()
        if command == "preview":
            result = api.preview_land_legal(**arguments)
        elif command == "save-summary":
            result = api.save_land_legal_summary(**arguments)
        else:
            result = api.list_land_legal_summaries(**arguments)
    except HostedProjectApiError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
