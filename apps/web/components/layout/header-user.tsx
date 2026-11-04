"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, LogOut, Settings, UserCircle } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useAuth } from "../auth/auth-context";
import { AccountProfileEditor } from "../auth/account-profile-editor";
import { accountDisplayName } from "../../lib/account-profile";

const ROLE_LABELS: Record<string, string> = {
  analyst: "분석 담당", viewer: "조회 전용", reviewer: "심사 담당",
  admin: "관리자", tenant_admin: "기관 관리자", platform_admin: "플랫폼 관리자",
};

export function HeaderUser() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = accountDisplayName(user);
  const role = user?.role ? ROLE_LABELS[user.role] ?? user.role.replaceAll("_", " ") : "";

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  const signOut = async () => {
    setPending(true);
    setError(null);
    try {
      await logout();
      router.replace("/login");
    } catch {
      setError("로그아웃하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setPending(false);
    }
  };

  return (
    <div ref={containerRef} className="relative shrink-0"
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.preventDefault();
          setOpen(false);
          triggerRef.current?.focus();
        }
      }}>
      <button ref={triggerRef} type="button" aria-label="내 계정 메뉴" aria-expanded={open} aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex h-11 items-center gap-2 rounded-re-control px-2 text-re-secondary hover:bg-re-subtle">
        <UserCircle className="h-6 w-6" aria-hidden="true" />
        <span className="hidden max-w-36 truncate text-sm font-medium sm:inline">{name}</span>
        <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {open ? (
        <div id={panelId} className="absolute right-0 top-12 z-40 w-64 max-w-[calc(100vw-2rem)] rounded-re-panel border border-re-border bg-re-surface p-2 shadow-lg">
          <div className="border-b border-re-border px-3 py-2">
            <p className="break-words text-sm font-semibold text-re-text">{name}</p>
            {role ? <p className="mt-1 text-xs text-re-muted">{role}</p> : null}
          </div>
          {editingName ? <div className="border-b border-re-border p-3"><AccountProfileEditor key={user?.userId} autoFocus onCancel={() => { setEditingName(false); triggerRef.current?.focus(); }} /></div>
            : <button type="button" onClick={() => setEditingName(true)} className="mt-1 flex min-h-11 w-full items-center gap-2 rounded-re-control px-3 text-sm text-re-secondary hover:bg-re-subtle">
              <UserCircle className="h-4 w-4" aria-hidden="true" />이름 변경
            </button>}
          <Link href="/settings" onClick={() => setOpen(false)}
            className="mt-1 flex min-h-11 items-center gap-2 rounded-re-control px-3 text-sm text-re-secondary hover:bg-re-subtle">
            <Settings className="h-4 w-4" aria-hidden="true" />계정·AI 설정
          </Link>
          <button type="button" disabled={pending} onClick={() => void signOut()}
            className="flex min-h-11 w-full items-center gap-2 rounded-re-control px-3 text-sm text-re-secondary hover:bg-re-subtle disabled:opacity-50">
            <LogOut className="h-4 w-4" aria-hidden="true" />{pending ? "로그아웃 중…" : "로그아웃"}
          </button>
          {error ? <p role="alert" className="px-3 py-2 text-xs text-re-danger">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
