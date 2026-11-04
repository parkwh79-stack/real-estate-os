"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import { editableAccountDisplayName, saveAccountDisplayName } from "../../lib/account-profile";
import { useAuth } from "./auth-context";

export function AccountProfileEditor({ onCancel, autoFocus = false }: { onCancel?: () => void; autoFocus?: boolean }) {
  const { user, refresh } = useAuth();
  const inputId = useId();
  const pendingRef = useRef(false);
  const [name, setName] = useState(() => editableAccountDisplayName(user));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    setError(null);
    setNotice(null);
    const trimmed = name.trim();
    if (!trimmed || trimmed.length > 200 || Array.from(trimmed).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
      || !editableAccountDisplayName(user ? { ...user, displayName: trimmed } : null)) {
      setError("계정 번호 대신 사용할 이름을 1~200자로 입력해 주세요.");
      return;
    }
    pendingRef.current = true;
    setPending(true);
    try {
      const saved = await saveAccountDisplayName(trimmed);
      setName(saved);
      setNotice("이름을 저장했습니다.");
      refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "이름을 저장하지 못했습니다.");
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return <form onSubmit={(event) => void save(event)} className="space-y-3" aria-label="표시 이름 변경">
    <div>
      <label htmlFor={inputId} className="text-sm font-medium text-re-text">표시 이름</label>
      <input id={inputId} value={name} onChange={(event) => { setName(event.target.value); setNotice(null); }}
        disabled={pending} maxLength={200} autoComplete="nickname" autoFocus={autoFocus} placeholder="사용할 이름을 입력하세요"
        aria-invalid={Boolean(error)} aria-describedby={error ? `${inputId}-error` : undefined}
        className="mt-1 w-full min-w-0 rounded-re-control border border-re-border bg-re-surface px-3 py-2 text-sm text-re-text disabled:opacity-60" />
    </div>
    <div className="flex flex-wrap gap-2">
      <button type="submit" disabled={pending} className="min-h-11 rounded-re-control bg-re-brand px-3 text-sm font-medium text-white disabled:opacity-60">
        {pending ? "저장 중…" : "이름 저장"}
      </button>
      {onCancel ? <button type="button" disabled={pending} onClick={onCancel} className="min-h-11 rounded-re-control px-3 text-sm text-re-secondary disabled:opacity-60">닫기</button> : null}
    </div>
    {error ? <p id={`${inputId}-error`} role="alert" className="text-xs leading-5 text-re-danger">{error}</p> : null}
    {notice ? <p role="status" className="text-xs leading-5 text-re-secondary">{notice}</p> : null}
  </form>;
}
