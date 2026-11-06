from __future__ import annotations

from typing import Any, Dict


def merge_source_documents(existing: list, incoming: list) -> list:
    """Append versions by immutable identity, never replace the document inventory.

    Filenames alone are not identities. Unidentified documents remain distinct;
    identical unidentified metadata is idempotent. Never mutate either input.
    """
    documents = [dict(item) if isinstance(item, dict) else item for item in existing]
    for item in incoming:
        if not isinstance(item, dict):
            if item not in documents:
                documents.append(item)
            continue
        identity = item.get("archiveId") or item.get("originalFileHash")
        index = next((i for i, old in enumerate(documents)
                      if identity and isinstance(old, dict)
                      and (old.get("archiveId") or old.get("originalFileHash")) == identity), None)
        if index is not None:
            documents[index] = {**documents[index], **item}
        elif item not in documents:
            documents.append(dict(item))
    return documents


def deep_merge(base: Dict[str, Any], override: Dict[str, Any] | None) -> Dict[str, Any]:
    """Merge project data with request overrides without mutating either object."""
    merged = dict(base)
    for key, value in (override or {}).items():
        if key == "source_documents" and isinstance(value, list):
            merged[key] = merge_source_documents(merged.get(key) or [], value)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
