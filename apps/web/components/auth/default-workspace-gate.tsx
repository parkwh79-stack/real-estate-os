"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import {
  AuthClientRequestError, prepareDefaultWorkspace, selectSupabaseTenant,
  simpleSignupEnabled, type DefaultWorkspace,
} from "../../lib/auth-client";
import { useAuth } from "./auth-context";

/** The server decides whether simple signup or the existing organization flow is active. */
export function DefaultWorkspaceGate({ children }: { children: ReactNode }) {
  const { status, user, refresh } = useAuth();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const existingBoundary = status === "authority_unavailable" || status === "tenant_selection";

  useEffect(() => {
    if (existingBoundary || !user?.userId) return;
    let active = true;
    simpleSignupEnabled().then((value) => {
      if (active) setEnabled(value);
    }).catch(() => {
      if (active) setFailed(true);
    });
    return () => { active = false; };
  }, [existingBoundary, user?.userId, attempt]);

  if (existingBoundary || enabled === false) return <>{children}</>;
  if (enabled === true) {
    return <PrepareWorkspace key={user?.userId} onTenantSelection={refresh} />;
  }
  return <WorkspaceStatus failed={failed} message={failed
    ? "내 작업공간 설정을 확인하지 못했습니다. 다시 확인해 주세요."
    : "로그인한 계정의 작업공간을 확인하고 있습니다."}
    retryLabel="다시 확인" onRetry={() => {
      setFailed(false);
      setAttempt((value) => value + 1);
    }} />;
}

function PrepareWorkspace({ onTenantSelection }: { onTenantSelection: () => void }) {
  const [failed, setFailed] = useState(false);
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const prepared = useRef<DefaultWorkspace | null>(null);

  const prepare = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setFailed(false);
    try {
      // The empty POST lets the server determine the caller's workspace and role.
      // Keep a successful result when only Tenant verification needs an explicit retry.
      const workspace = prepared.current ?? await prepareDefaultWorkspace();
      prepared.current = workspace;
      if (!mounted.current) return;
      await selectSupabaseTenant(workspace.tenantId);
      if (mounted.current) window.location.assign("/dashboard");
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof AuthClientRequestError && error.code === "tenant_selection_required") {
        onTenantSelection();
      } else {
        setFailed(true);
      }
    } finally {
      inFlight.current = false;
    }
  }, [onTenantSelection]);

  useEffect(() => {
    mounted.current = true;
    void prepare();
    return () => { mounted.current = false; };
  }, [prepare]);

  return <WorkspaceStatus failed={failed}
    message={failed ? "내 작업공간을 준비하지 못했습니다. 다시 시도해 주세요."
      : "내 작업공간을 준비하고 있습니다. 완료되면 대시보드로 이동합니다."}
    retryLabel="다시 시도" onRetry={() => void prepare()} />;
}

function WorkspaceStatus({ failed, message, retryLabel, onRetry }: {
  failed: boolean; message: string; retryLabel: string; onRetry: () => void;
}) {
  const { logout } = useAuth();
  return <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 text-slate-950">
    <section data-testid="default-workspace-status" className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white p-7 shadow-sm">
      {failed ? <AlertCircle className="h-8 w-8 text-amber-600" /> : <LoaderCircle className="h-8 w-8 animate-spin text-blue-700" />}
      <h1 className="mt-4 text-2xl font-black">내 작업공간</h1>
      <p role={failed ? "alert" : "status"} className="mt-3 text-sm leading-6 text-slate-600">{message}</p>
      {failed ? <div className="mt-6 flex gap-2">
        <button type="button" onClick={onRetry} className="inline-flex h-10 items-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-bold text-white">
          <RefreshCw className="h-4 w-4" />{retryLabel}
        </button>
        <button type="button" onClick={() => void logout().then(() => window.location.assign("/login?switch=1"))}
          className="h-10 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold">로그아웃</button>
      </div> : null}
    </section>
  </main>;
}
