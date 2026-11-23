from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep

import pytest

from db.repositories import identity_membership_repository as repository_module
from db.repositories.identity_membership_repository import (
    IdentityMembershipRepositoryUnavailable,
    IdentityPrincipalRecord,
    PrincipalMembershipDirectory,
    TenantMembershipRecord,
    load_directory_singleflight,
)


ISSUER = "https://project.supabase.co/auth/v1"
SUBJECT = "identity-singleflight-subject"


def _directory(*, status: str = "active", version: int = 1):
    return PrincipalMembershipDirectory(
        principal=IdentityPrincipalRecord(
            issuer=ISSUER,
            subject=SUBJECT,
            principal_type="human",
            status=status,
            email=None,
            display_name=None,
            legacy_user_id=None,
        ),
        memberships=(
            TenantMembershipRecord(
                tenant_id="tenant-a",
                tenant_status="active",
                role="analyst",
                status=status,
                membership_version=version,
            ),
        ),
    )


class _BlockingRepository:
    def __init__(self, result: PrincipalMembershipDirectory) -> None:
        self.result = result
        self.error: BaseException | None = None
        self.entered = Event()
        self.release = Event()
        self._lock = Lock()
        self.calls = 0

    def load_directory(self, *, issuer: str, subject: str):
        assert issuer == ISSUER
        assert subject == SUBJECT
        with self._lock:
            self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=2), "test repository was not released"
        if self.error is not None:
            raise self.error
        return self.result


def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def _has_follower() -> bool:
    with repository_module._directory_lookup_flights_lock:
        return any(
            flight.followers >= 1
            for flight in repository_module._directory_lookup_flights.values()
        )


def test_identical_concurrent_directory_reads_share_one_query_only() -> None:
    repository = _BlockingRepository(_directory(version=7))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            load_directory_singleflight,
            repository,
            issuer=ISSUER + "///",
            subject=f"  {SUBJECT}  ",
        )
        assert repository.entered.wait(timeout=1)
        second = executor.submit(
            load_directory_singleflight,
            repository,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        _wait_until(_has_follower)
        repository.release.set()
        assert first.result(timeout=1) == second.result(timeout=1)

    assert repository.calls == 1


def test_completed_result_is_not_cached_and_revocation_is_observed() -> None:
    repository = _BlockingRepository(_directory(version=1))
    repository.release.set()

    active = load_directory_singleflight(
        repository,
        issuer=ISSUER,
        subject=SUBJECT,
    )
    repository.result = _directory(status="revoked", version=2)
    revoked = load_directory_singleflight(
        repository,
        issuer=ISSUER,
        subject=SUBJECT,
    )

    assert repository.calls == 2
    assert active.memberships[0].status == "active"
    assert revoked.memberships[0].status == "revoked"
    assert revoked.memberships[0].membership_version == 2


def test_concurrent_failure_is_shared_then_next_call_retries() -> None:
    repository = _BlockingRepository(_directory(version=3))
    repository.error = IdentityMembershipRepositoryUnavailable("database down")

    def lookup():
        return load_directory_singleflight(
            repository,
            issuer=ISSUER,
            subject=SUBJECT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(lookup)
        assert repository.entered.wait(timeout=1)
        second = executor.submit(lookup)
        _wait_until(_has_follower)
        repository.release.set()
        with pytest.raises(IdentityMembershipRepositoryUnavailable):
            first.result(timeout=1)
        with pytest.raises(IdentityMembershipRepositoryUnavailable):
            second.result(timeout=1)

    assert repository.calls == 1
    repository.error = None
    recovered = lookup()
    assert recovered.memberships[0].membership_version == 3
    assert repository.calls == 2
