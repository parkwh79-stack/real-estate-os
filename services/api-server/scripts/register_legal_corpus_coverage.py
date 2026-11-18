"""Register one reviewed official-corpus contract set after DB migration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.postgres_runtime_privileges import (  # noqa: E402
    DEFAULT_RUNTIME_SCHEMA,
    RUNTIME_SCHEMA_ENV,
)
from db.repositories.legal_corpus_coverage_registration_repository import (  # noqa: E402
    PostgresLegalCorpusCoverageRegistrationRepository,
)
from use_cases.legal_corpus_coverage_registration import (  # noqa: E402
    LegalCorpusCoverageRegistrationError,
    LegalCorpusCoverageRegistrationService,
    load_coverage_contract_set_manifest,
)


MIGRATION_DATABASE_URL_ENV = "REALYTICS_MIGRATION_DATABASE_URL"
STATUS_DRIFT_EXIT_CODE = 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and atomically register exactly eight reviewed "
            "official legal-corpus coverage identities using the "
            "deployment/migration database role."
        )
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="UTF-8 JSON contract-set manifest reviewed for this deployment.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate DB state and show the action without writing.",
    )
    mode.add_argument(
        "--status",
        action="store_true",
        help=(
            "Return 0 only when this exact manifest is the current leaf; "
            f"return {STATUS_DRIFT_EXIT_CODE} when registration is pending."
        ),
    )
    parser.add_argument(
        "--schema",
        default=(
            os.environ.get(RUNTIME_SCHEMA_ENV)
            or DEFAULT_RUNTIME_SCHEMA
        ),
        help=(
            "Private application schema. Defaults to "
            f"{RUNTIME_SCHEMA_ENV} or {DEFAULT_RUNTIME_SCHEMA!r}."
        ),
    )
    args = parser.parse_args(argv)

    dsn = str(os.environ.get(MIGRATION_DATABASE_URL_ENV) or "").strip()
    if not dsn:
        raise RuntimeError(
            f"Set {MIGRATION_DATABASE_URL_ENV} to the deployment-role DSN. "
            "The runtime and legal-sync DSNs cannot register coverage "
            "contracts."
        )

    try:
        plan = load_coverage_contract_set_manifest(args.manifest)
        repository = (
            PostgresLegalCorpusCoverageRegistrationRepository(
                connection_factory=lambda: postgres.connect(dsn),
                schema_name=args.schema,
            )
        )
        service = LegalCorpusCoverageRegistrationService(repository)
        result = (
            service.inspect(plan)
            if args.dry_run or args.status
            else service.register(plan)
        )
    except LegalCorpusCoverageRegistrationError as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            result.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if args.status and result.action != "current":
        return STATUS_DRIFT_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
