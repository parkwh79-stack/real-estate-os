"""User-owned AI choices in the existing tenant-RLS profile table; no bootstrap DDL."""
from __future__ import annotations

from contextlib import contextmanager
import json

from db.postgres_runtime_pool import api_tenant_transaction


class UserAIProfileUnavailable(RuntimeError):
    pass


_COLUMNS = ("user_id", "tenant_id", "provider", "model", "status", "api_key_encrypted", "api_key_masked",
            "allow_system_fallback", "fallback_provider", "fallback_model", "created_at", "updated_at", "metadata_json")


class PostgresUserAIProfileRepository:
    def __init__(self, *, tenant_id: str, user_id: str, transaction_factory=api_tenant_transaction):
        if not all(isinstance(value, str) and value.strip() for value in (tenant_id, user_id)):
            raise ValueError("Verified AI profile tenant and user are required")
        self.tenant_id, self.user_id, self._transaction = tenant_id, user_id, transaction_factory

    @contextmanager
    def _scope(self):
        try:
            with self._transaction(self.tenant_id) as connection:
                connection.execute("SELECT set_config('realytics.user_id', %s, true)", (self.user_id,))
                yield connection
        except UserAIProfileUnavailable:
            raise
        except Exception:
            # Storage errors are not an absent user choice. Never fall back to
            # app_state or include SQL/credential parameters in a public error.
            raise UserAIProfileUnavailable("Normalized user AI profile authority is unavailable") from None

    def get(self):
        with self._scope() as connection:
            row = connection.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM realytics.user_ai_profiles WHERE tenant_id = %s AND user_id = %s",
                (self.tenant_id, self.user_id),
            ).fetchone()
        if row is None:
            return None
        values = dict(zip(_COLUMNS, row, strict=True))
        if values["tenant_id"] != self.tenant_id or values["user_id"] != self.user_id:
            raise UserAIProfileUnavailable("AI profile scope could not be verified")
        metadata = values["metadata_json"] or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else dict(metadata)
        credential = metadata.pop("_sessionCredential", {})
        credential = credential if isinstance(credential, dict) else {}
        return {"userId": values["user_id"], "tenantId": values["tenant_id"], "provider": values["provider"],
                "model": values["model"], "status": values["status"], "apiKeyEncrypted": values["api_key_encrypted"],
                "apiKeyMasked": values["api_key_masked"], "credentialRef": credential.get("reference"),
                "credentialBackend": credential.get("backend"),
                "apiKeyConfigured": bool(values["api_key_encrypted"] or credential.get("reference")),
                "allowSystemFallback": bool(values["allow_system_fallback"]),
                "fallbackProvider": values["fallback_provider"], "fallbackModel": values["fallback_model"],
                "createdAt": values["created_at"].isoformat() if hasattr(values["created_at"], "isoformat") else values["created_at"],
                "updatedAt": values["updated_at"].isoformat() if hasattr(values["updated_at"], "isoformat") else values["updated_at"],
                "metadata": metadata}

    def save(self, profile: dict):
        if profile.get("tenantId") != self.tenant_id or profile.get("userId") != self.user_id:
            raise ValueError("AI profile does not match verified scope")
        metadata = dict(profile.get("metadata") or {})
        # The existing Postgres schema has no credential_ref columns. Store
        # only the vault reference in JSONB, stripping it from public metadata.
        metadata.pop("_sessionCredential", None)
        if profile.get("credentialRef"):
            metadata["_sessionCredential"] = {"reference": profile["credentialRef"], "backend": profile.get("credentialBackend")}
        params = (self.user_id, self.tenant_id, profile["provider"], profile["model"], profile["status"],
                  profile.get("apiKeyEncrypted"), profile.get("apiKeyMasked"), int(profile["allowSystemFallback"]),
                  profile.get("fallbackProvider"), profile.get("fallbackModel"), profile["createdAt"], profile["updatedAt"],
                  json.dumps(metadata, ensure_ascii=False))
        updates = ", ".join(f"{column}=excluded.{column}" for column in _COLUMNS if column not in {"user_id", "tenant_id", "created_at"})
        with self._scope() as connection:
            row = connection.execute(
                f"INSERT INTO realytics.user_ai_profiles ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * 12)}, %s::jsonb) ON CONFLICT (user_id) DO UPDATE SET {updates} "
                "WHERE user_ai_profiles.tenant_id = excluded.tenant_id RETURNING user_id",
                params,
            ).fetchone()
            if row is None or row[0] != self.user_id:
                raise UserAIProfileUnavailable("AI profile update could not be verified")

    def delete(self):
        with self._scope() as connection:
            row = connection.execute(
                "DELETE FROM realytics.user_ai_profiles WHERE tenant_id = %s AND user_id = %s RETURNING user_id",
                (self.tenant_id, self.user_id),
            ).fetchone()
        return row is not None
