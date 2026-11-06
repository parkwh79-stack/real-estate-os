"""Update only the authenticated human's normalized display name."""
from db.postgres_runtime_pool import api_tenant_transaction


class AccountProfileUnavailable(RuntimeError):
    pass


class AccountProfileNotActive(RuntimeError):
    pass


class PostgresAccountProfileRepository:
    def __init__(self, transaction_factory=api_tenant_transaction):
        self._transaction = transaction_factory

    def update_display_name(self, *, tenant_id: str, issuer: str, subject: str, display_name: str) -> str:
        if not all(isinstance(value, str) and value.strip() for value in (tenant_id, issuer, subject, display_name)):
            raise ValueError("Verified account scope and display name are required")
        try:
            with self._transaction(tenant_id) as connection:
                row = connection.execute(
                    """
                    UPDATE realytics.principals AS principal
                       SET display_name = %s, updated_at = statement_timestamp()
                     WHERE principal.issuer = %s AND principal.subject = %s
                       AND principal.status = 'active' AND principal.principal_type = 'human'
                       AND EXISTS (
                           SELECT 1 FROM realytics.tenant_memberships AS membership
                           JOIN realytics.tenants AS tenant ON tenant.tenant_id = membership.tenant_id
                           WHERE membership.issuer = principal.issuer
                             AND membership.subject = principal.subject
                             AND membership.tenant_id = %s
                             AND membership.status = 'active' AND tenant.status = 'active'
                       )
                    RETURNING display_name
                    """,
                    (display_name, issuer, subject, tenant_id),
                ).fetchone()
                if row is None:
                    raise AccountProfileNotActive("Account membership is no longer active")
                if row[0] != display_name:
                    raise AccountProfileUnavailable("Saved display name could not be verified")
                return str(row[0])
        except (AccountProfileNotActive, AccountProfileUnavailable):
            raise
        except Exception as exc:
            raise AccountProfileUnavailable("Account profile authority is unavailable") from exc
