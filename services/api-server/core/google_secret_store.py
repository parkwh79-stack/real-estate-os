"""Google Secret Manager storage for ephemeral, session-bound BYOK envelopes.

Runtime ADC comes from the Cloud Run service identity; no service-account key
file is created. Static deployment secrets use a separate name/IAM boundary.
"""

from __future__ import annotations

import json
import os
import re
from datetime import timedelta
from typing import Any
from uuid import uuid4


_PREFIX = "realytics-user-ai-"
_TIMEOUT = 10.0


def google_secret_config() -> tuple[str, str]:
    project = (os.environ.get("REALYTICS_GCP_PROJECT") or "").strip()
    location = (os.environ.get("REALYTICS_GCP_SECRET_LOCATION") or "").strip()
    if not re.fullmatch(r"(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})", project):
        raise RuntimeError("REALYTICS_GCP_PROJECT must be an explicit project ID or number")
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]+", location):
        raise RuntimeError("REALYTICS_GCP_SECRET_LOCATION must be an explicit Google region")
    return project, location


def _resource(reference: str) -> str | None:
    project, _ = google_secret_config()
    pattern = (
        rf"gcp-sm://projects/{re.escape(project)}/secrets/{_PREFIX}"
        r"[0-9a-f]{32}/versions/[1-9][0-9]*"
    )
    if not re.fullmatch(pattern, reference):
        return None
    return reference.removeprefix("gcp-sm://")


def _client():
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient(transport="rest")


def create_record(envelope: dict[str, Any], *, ttl_seconds: int) -> str:
    import google_crc32c

    project, location = google_secret_config()
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > 65536:
        raise ValueError("Credential envelope exceeds the secret storage limit")
    secret_id = _PREFIX + uuid4().hex
    name = f"projects/{project}/secrets/{secret_id}"
    try:
        with _client() as client:
            client.create_secret(
                request={
                    "parent": f"projects/{project}",
                    "secret_id": secret_id,
                    "secret": {
                        "replication": {
                            "user_managed": {"replicas": [{"location": location}]}
                        },
                        # Cloud cleanup needs >=60s. The envelope's exact TTL
                        # remains authoritative even when cleanup happens later.
                        "ttl": timedelta(seconds=max(120, ttl_seconds + 60)),
                    },
                },
                retry=None,
                timeout=_TIMEOUT,
            )
            try:
                version = client.add_secret_version(
                    request={
                        "parent": name,
                        "payload": {
                            "data": payload,
                            "data_crc32c": google_crc32c.value(payload),
                        },
                    },
                    retry=None,
                    timeout=_TIMEOUT,
                )
                number = version.name.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[1-9][0-9]*", number):
                    raise RuntimeError("Invalid secret version")
            except Exception:
                # Delete only the unique secret created by this operation.
                # If cleanup also fails, the cloud TTL still bounds retention.
                try:
                    client.delete_secret(request={"name": name}, retry=None, timeout=_TIMEOUT)
                except Exception:
                    pass
                raise
    except Exception:
        # SDK errors can contain request metadata. Never expose their text.
        raise RuntimeError("Google Secret Manager credential creation unavailable") from None
    return f"gcp-sm://{name}/versions/{number}"


def read_record(reference: str) -> dict[str, Any] | None:
    name = _resource(reference)
    if name is None:
        return None
    import google_crc32c
    from google.api_core.exceptions import NotFound

    try:
        with _client() as client:
            response = client.access_secret_version(
                request={"name": name}, retry=None, timeout=_TIMEOUT
            )
        data = bytes(response.payload.data)
        if google_crc32c.value(data) != response.payload.data_crc32c:
            raise RuntimeError("Secret integrity check failed")
        record = json.loads(data.decode("utf-8"))
    except NotFound:
        return None
    except (ValueError, UnicodeError):
        return None
    except Exception:
        raise RuntimeError("Google Secret Manager credential read unavailable") from None
    if not isinstance(record, dict) or record.get("schemaVersion") != "realytics-session-secret-v1":
        return None
    return record


def delete_record(reference: str) -> bool:
    name = _resource(reference)
    if name is None:
        return False
    from google.api_core.exceptions import NotFound

    try:
        with _client() as client:
            client.delete_secret(
                request={"name": name.rsplit("/versions/", 1)[0]},
                retry=None,
                timeout=_TIMEOUT,
            )
    except NotFound:
        return False
    except Exception:
        raise RuntimeError("Google Secret Manager credential deletion unavailable") from None
    return True
