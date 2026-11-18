"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, CheckCircle2, Loader2, MessageCircle } from "lucide-react";
import {
  exchangeOauthSession,
} from "../../../../lib/auth-client";

function safeRedirectPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("://")) {
    return "/start";
  }
  if (value === "/" || value === "/dashboard") return "/start";
  return value;
}

export default function KakaoSessionPage() {
  return (
    <Suspense fallback={<SessionShell status="loading" message="소셜 인증 세션을 연결하는 중입니다." />}>
      <KakaoSessionConnector />
    </Suspense>
  );
}

function KakaoSessionConnector() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionId = searchParams.get("session_id") || searchParams.get("sessionId");
  const redirectPath = useMemo(() => safeRedirectPath(searchParams.get("redirect")), [searchParams]);
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("소셜 로그인 세션을 REALYTICS에 연결하는 중입니다.");

  useEffect(() => {
    window.history.replaceState(null, "", window.location.pathname);
    let active = true;
    async function connect() {
      if (!sessionId) {
        setStatus("error");
        setMessage("세션 ID가 없습니다. 로그인 링크를 다시 생성해 주세요.");
        return;
      }
      try {
        await exchangeOauthSession(sessionId);
        if (!active) return;
        setStatus("success");
        setMessage("소셜 로그인 세션이 성공적으로 연결되었습니다. REALYTICS OS 런처로 이동합니다.");
        window.setTimeout(() => {
          router.replace(redirectPath);
        }, 700);
      } catch (err) {
        if (!active) return;
        setStatus("error");
        setMessage(err instanceof Error ? err.message : "카카오 세션 연결에 실패했습니다.");
      }
    }
    void connect();
    return () => {
      active = false;
    };
  }, [redirectPath, router, sessionId]);

  return (
    <SessionShell
      status={status}
      message={message}
      redirectPath={redirectPath}
    />
  );
}

function SessionShell({
  status,
  message,
  redirectPath = "/start",
}: {
  status: "loading" | "success" | "error";
  message: string;
  redirectPath?: string;
}) {
  const Icon = status === "success" ? CheckCircle2 : status === "error" ? AlertCircle : Loader2;
  const tone = status === "success"
    ? "text-emerald-600"
    : status === "error"
      ? "text-rose-600"
      : "text-amber-600";
  return (
    <main className="mx-auto flex min-h-[calc(100vh-72px)] max-w-lg items-center px-5 py-8">
      <section className="w-full rounded-3xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-50 text-amber-600 dark:bg-amber-950/30">
          <MessageCircle className="h-7 w-7" />
        </div>
        <p className="mt-5 text-xs font-bold uppercase text-slate-500">Kakao Login</p>
        <h1 className="mt-2 text-2xl font-black text-slate-950 dark:text-white">
          REALYTICS 세션 연결
        </h1>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Icon className={`h-5 w-5 ${tone} ${status === "loading" ? "animate-spin" : ""}`} />
          <p className="text-sm font-bold text-slate-700 dark:text-slate-200">{message}</p>
        </div>
        <p className="mt-4 text-xs leading-5 text-slate-500">
          카카오톡 알림에는 민감한 PF 정보가 포함되지 않습니다. 상세 검토는 REALYTICS 로그인 세션 안에서만 진행합니다.
        </p>
        <div className="mt-6 flex flex-col gap-2">
          <Link
            href={redirectPath}
            className="inline-flex h-11 items-center justify-center rounded-xl bg-slate-950 px-4 text-sm font-black text-white dark:bg-white dark:text-slate-950"
          >
            OS 런처로 이동
          </Link>
          <Link
            href="/settings"
            className="inline-flex h-11 items-center justify-center rounded-xl border border-slate-200 px-4 text-sm font-bold text-slate-700 dark:border-slate-700 dark:text-slate-200"
          >
            로그인 설정 확인
          </Link>
        </div>
      </section>
    </main>
  );
}
