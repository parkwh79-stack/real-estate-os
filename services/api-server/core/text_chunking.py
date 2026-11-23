from __future__ import annotations

"""Lossless character-bounded chunking for document extraction prompts."""

from collections.abc import Callable


def chunk_text_preserving_context(
    text: str,
    *,
    max_chars: int,
    is_context_line: Callable[[str], bool] | None = None,
) -> list[str]:
    """Split all input text without dropping the tail.

    A page/sheet marker is repeated when a chunk boundary falls inside that
    page/sheet, so downstream locators retain their local context. Repeated
    context is metadata, not a substitute for omitted source text.
    """

    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    predicate = is_context_line or (lambda _line: False)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_size = 0
    context_line = ""

    def flush() -> None:
        nonlocal current_parts, current_size
        if not current_parts:
            return
        chunks.append("".join(current_parts))
        current_parts = []
        current_size = 0

    def context_prefix(*, is_context: bool) -> str:
        if is_context or not context_line:
            return ""
        prefix = f"{context_line}\n"
        # Context is synthetic metadata. Never let an unusually long marker
        # crowd all source characters out of a chunk.
        return prefix if len(prefix) < max_chars else ""

    def seed_context(*, is_context: bool) -> None:
        nonlocal current_size
        if current_parts:
            return
        prefix = context_prefix(is_context=is_context)
        if prefix:
            current_parts.append(prefix)
            current_size = len(prefix)

    for source_piece in text.splitlines(keepends=True):
        source_line = source_piece.rstrip("\r\n")
        is_context = bool(predicate(source_line))
        if is_context:
            context_line = source_line

        prefix_size = len(context_prefix(is_context=is_context))
        fresh_capacity = max_chars - prefix_size
        available = max_chars - current_size
        # Preserve normal lines as units when they fit in a fresh chunk. This
        # keeps table rows and locator markers readable without sacrificing any
        # source characters.
        if current_parts and len(source_piece) > available:
            if is_context or len(source_piece) <= fresh_capacity:
                flush()

        remaining = source_piece
        while remaining:
            seed_context(is_context=is_context)
            available = max_chars - current_size
            if available <= 0:
                flush()
                continue
            if len(remaining) <= available:
                current_parts.append(remaining)
                current_size += len(remaining)
                break
            if current_parts and len(remaining) <= fresh_capacity:
                flush()
                continue
            # A single OCR paragraph or spreadsheet cell may itself be larger
            # than a prompt chunk. Emit every segment and keep its context.
            segment = remaining[:available]
            current_parts.append(segment)
            current_size += len(segment)
            remaining = remaining[available:]
            flush()
    flush()
    return chunks or [""]


__all__ = ["chunk_text_preserving_context"]
