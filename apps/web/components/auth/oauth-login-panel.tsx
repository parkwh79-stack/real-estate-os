"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, LoaderCircle, MessageCircle } from "lucide-react";
import {
  loadSupabaseAuthReadiness,
  signInWithSupabaseOAuth,
  type SupabaseAuthReadiness,
} from "../../lib/auth-client";

type Provider = "google" | "kakao" | "naver";

export function OAuthLoginPanel() {
  const [readiness, setReadiness] = useState<SupabaseAuthReadiness | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void loadSupabaseAuthReadiness()
      .then((value) => {
        if (active) setReadiness(value);
      })
      .catch(() => {
        if (active) setError("연결 계정 로그인 상태를 확인하지 못했습니다.");
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const providers = useMemo(
    () => (["google", "kakao", "naver"] as Provider[]).filter((provider) => (
      readiness?.providerUsable?.[provider]
    )),
    [readiness],
  );

  async function start(provider: Provider) {
    setBusy(provider);
    setError(null);
    try {
      await signInWithSupabaseOAuth(provider, resolveNextPath(), {
        switchAccount: new URLSearchParams(window.location.search).get("switch") === "1",
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "연결 계정 로그인을 시작하지 못했습니다.");
      setBusy(null);
    }
  }

  if (checking) {
    return (
      <p aria-live="polite" className="flex items-center justify-center py-5 text-sm text-slate-500">
        <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
        연결 계정 확인 중
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {error ? (
        <p role="alert" className="flex gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs leading-5 text-rose-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}
      {providers.includes("google") ? (
        <button
          type="button"
          onClick={() => void start("google")}
          disabled={busy !== null}
          className="flex h-11 w-full items-center justify-center gap-3 rounded-xl border border-slate-300 bg-white text-sm font-bold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
        >
          {busy === "google" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <GoogleMark />}
          Google 계정으로 로그인
        </button>
      ) : null}
      {providers.includes("kakao") ? (
        <button
          type="button"
          onClick={() => void start("kakao")}
          disabled={busy !== null}
          className="flex h-11 w-full items-center justify-center gap-3 rounded-xl border border-[#e7d000] bg-[#FEE500] text-sm font-bold text-[#191919] hover:bg-[#f5dc00] disabled:opacity-60"
        >
          {busy === "kakao" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4 fill-current" />}
          카카오로 계속하기
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => void start("naver")}
        disabled={busy !== null || !providers.includes("naver")}
        className="flex h-11 w-full items-center justify-center gap-3 rounded-xl border border-[#028b3f] bg-[#03a94e] text-sm font-bold text-white hover:bg-[#028b3f] disabled:opacity-60"
      >
        {busy === "naver" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
        {providers.includes("naver") ? "네이버로 계속하기" : "네이버 로그인 · 준비 중"}
      </button>
      {!providers.length ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
          현재 사용할 수 있는 연결 계정 공급자가 없습니다. 이메일 로그인을 이용해 주세요.
        </p>
      ) : null}
      <p className="text-[11px] leading-5 text-slate-500">
        처음 이용하면 로그인 계정과 작업공간을 확인한 뒤 안내합니다.
      </p>
    </div>
  );
}

function resolveNextPath() {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next || next === "/" || next === "/dashboard") return "/start";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/start";
}

function GoogleMark() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.07 5.07 0 0 1-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09Z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23Z" />
      <path fill="#FBBC05" d="M5.84 14.09A6.6 6.6 0 0 1 5.49 12c0-.73.13-1.43.35-2.09V7.06H2.18A11 11 0 0 0 1 12c0 1.78.43 3.45 1.18 4.94l3.66-2.85Z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38Z" />
    </svg>
  );
}
