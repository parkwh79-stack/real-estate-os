"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { AlertCircle, CheckCircle2, LoaderCircle } from "lucide-react";
import { OAuthLoginPanel } from "./oauth-login-panel";
import {
  AuthClientRequestError,
  REALYTICS_PRIVACY_VERSION,
  REALYTICS_TERMS_VERSION,
  requestSupabasePasswordRecovery,
  resendSupabaseSignupConfirmation,
  signInWithSupabasePassword,
  signUpWithSupabasePassword,
  updateSupabasePassword,
} from "../../lib/auth-client";

type Mode = "login" | "signup" | "recovery" | "reset" | "confirmation";

export function AccountAccessPanel() {
  const [ready, setReady] = useState(false);
  const [switchAccount, setSwitchAccount] = useState(false);
  const [mode, setMode] = useState<Mode>("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resendCooldown, setResendCooldown] = useState(0);
  const resendCoolingDown = resendCooldown > 0;

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("mode") === "signup") {
      setMode("signup");
    } else if (query.get("mode") === "reset" || query.get("flow") === "password_recovery") {
      setMode("reset");
    }
    const code = query.get("error");
    if (code) setError(loginErrorMessage(code));
    setSwitchAccount(query.get("switch") === "1");
    setReady(true);
  }, []);

  useEffect(() => {
    if (!resendCoolingDown) return;
    const timer = window.setInterval(() => {
      setResendCooldown((value) => Math.max(0, value - 1));
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [resendCoolingDown]);

  function changeMode(next: Mode) {
    setMode(next);
    setPassword("");
    setPasswordConfirmation("");
    setError(null);
    setNotice(null);
    setResendCooldown(0);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (mode === "signup") {
      if (!displayName.trim()) return setError("표시명을 입력해 주세요.");
      const passwordIssue = newPasswordIssue(password);
      if (passwordIssue) return setError(passwordIssue);
      if (password !== passwordConfirmation) return setError("비밀번호 확인이 일치하지 않습니다.");
      if (!termsAccepted || !privacyAccepted) {
        return setError("필수 이용약관과 개인정보 처리방침을 모두 확인해 주세요.");
      }
    }
    if (mode === "reset") {
      const passwordIssue = newPasswordIssue(password);
      if (passwordIssue) return setError(passwordIssue);
      if (password !== passwordConfirmation) return setError("비밀번호 확인이 일치하지 않습니다.");
    }

    setBusy(true);
    try {
      if (mode === "login") {
        const result = await signInWithSupabasePassword({ email, password });
        window.location.assign(result.next);
      } else if (mode === "signup") {
        const result = await signUpWithSupabasePassword({
          email,
          password,
          displayName,
          termsAccepted: true,
          privacyAccepted: true,
          termsVersion: REALYTICS_TERMS_VERSION,
          privacyVersion: REALYTICS_PRIVACY_VERSION,
        });
        if (result.requiresEmailConfirmation) {
          setMode("confirmation");
          setResendCooldown(60);
        } else {
          window.location.assign(result.next);
        }
      } else if (mode === "recovery") {
        await requestSupabasePasswordRecovery(email);
        setNotice("가입된 계정이고 발송이 가능하면 비밀번호 재설정 메일이 전송됩니다. 계정 존재 여부와 실제 발송 여부는 이 화면에서 구분하지 않습니다.");
        setResendCooldown(60);
      } else if (mode === "reset") {
        await updateSupabasePassword(password);
        changeMode("login");
        setNotice("비밀번호를 변경했습니다. 새 비밀번호로 로그인해 주세요.");
      }
    } catch (caught) {
      if (
        caught instanceof AuthClientRequestError
        && caught.code === "auth_email_rate_limited"
      ) {
        setResendCooldown(60);
      }
      setError(accountErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function resendConfirmation() {
    if (busy || resendCooldown > 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await resendSupabaseSignupConfirmation(email);
      setNotice("확인 가능한 가입 요청이라면 메일이 발송됩니다. 스팸함도 확인해 주세요.");
      setResendCooldown(60);
    } catch (caught) {
      if (
        caught instanceof AuthClientRequestError
        && caught.code === "auth_email_rate_limited"
      ) {
        setResendCooldown(60);
      }
      setError(accountErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (mode === "confirmation") {
    return (
      <div data-testid="email-confirmation-state" className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-emerald-950">
        <CheckCircle2 className="h-6 w-6 text-emerald-600" />
        <h3 className="mt-3 text-lg font-black">이메일을 확인해 주세요</h3>
        <p className="mt-2 text-sm leading-6 text-emerald-800">
          <strong>{email}</strong>이 가입 가능한 주소라면 확인 링크가 발송됩니다.
          링크를 확인하면 로그인 계정에 맞는 작업공간으로 안내합니다.
        </p>
        <p className="mt-2 text-xs leading-5 text-emerald-800">
          이미 가입한 주소라도 계정 존재 여부 보호를 위해 같은 화면이 표시됩니다. 로그인 또는 비밀번호 찾기를 이용해 주세요.
        </p>
        {error ? <p role="alert" className="mt-4 flex gap-2 rounded-xl border border-rose-200 bg-white p-3 text-xs leading-5 text-rose-800"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</p> : null}
        {notice ? <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-white p-3 text-xs leading-5 text-emerald-800">{notice}</p> : null}
        <div className="mt-4 flex flex-wrap gap-3">
          <button type="button" onClick={() => void resendConfirmation()} disabled={busy || resendCooldown > 0} className="rounded-lg border border-emerald-300 bg-white px-3 py-2 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-60">
            {busy ? "요청 중" : resendCooldown > 0 ? `다시 보내기 (${resendCooldown}초)` : "확인 메일 다시 보내기"}
          </button>
          <button type="button" onClick={() => changeMode("login")} className="px-2 py-2 text-sm font-bold underline underline-offset-4">
            로그인으로 돌아가기
          </button>
        </div>
      </div>
    );
  }

  return (
    <fieldset disabled={!ready || busy} aria-busy={!ready || busy} className="min-w-0 border-0 p-0">
      {switchAccount ? <p role="status" className="mb-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">계정 전환을 위해 로그아웃했습니다. 사용할 계정으로 다시 로그인해 주세요.</p> : null}
      {mode === "login" || mode === "signup" ? (
        <div className="grid grid-cols-2 rounded-xl bg-slate-100 p-1" role="tablist" aria-label="계정 접근 방식">
          <button type="button" role="tab" aria-selected={mode === "login"} onClick={() => changeMode("login")} className={`h-10 rounded-lg text-sm font-bold ${mode === "login" ? "bg-white text-slate-950 shadow-sm" : "text-slate-500"}`}>
            로그인
          </button>
          <button type="button" role="tab" aria-selected={mode === "signup"} onClick={() => changeMode("signup")} className={`h-10 rounded-lg text-sm font-bold ${mode === "signup" ? "bg-white text-slate-950 shadow-sm" : "text-slate-500"}`}>
            처음이신가요? 가입 신청
          </button>
        </div>
      ) : null}

      <form onSubmit={submit} className="mt-5 space-y-4">
        {mode === "signup" ? <Field label="표시명" name="name" value={displayName} onChange={setDisplayName} autoComplete="name" /> : null}
        {mode !== "reset" ? <Field label="이메일" name="email" type="email" value={email} onChange={setEmail} autoComplete="email" /> : null}
        {mode !== "recovery" ? <Field label={mode === "reset" ? "새 비밀번호" : "비밀번호"} name="password" type="password" value={password} onChange={setPassword} autoComplete={mode === "login" ? "current-password" : "new-password"} hint={mode === "signup" || mode === "reset" ? "12~128자 · 대/소문자·숫자·기호 포함" : undefined} /> : null}
        {mode === "signup" || mode === "reset" ? <Field label="비밀번호 확인" name="password-confirmation" type="password" value={passwordConfirmation} onChange={setPasswordConfirmation} autoComplete="new-password" /> : null}

        {mode === "signup" ? (
          <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs leading-5 text-slate-700">
            <Consent checked={termsAccepted} onChange={setTermsAccepted} label={`[필수] 서비스 이용약관 (${REALYTICS_TERMS_VERSION})`} />
            <Consent checked={privacyAccepted} onChange={setPrivacyAccepted} label={`[필수] 개인정보 처리방침 (${REALYTICS_PRIVACY_VERSION})`} />
            <p className="text-[11px] text-slate-500">
              현재 정책 버전과 동의 여부는 가입 BFF가 먼저 검증합니다. 최종 동의는 이메일 인증 후 온보딩 프로필에 정책 버전과 시각으로 다시 기록됩니다.
            </p>
          </div>
        ) : null}

        {error ? <p role="alert" className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs leading-5 text-rose-800"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</p> : null}
        {notice ? <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-800">{notice}</p> : null}

        <button type="submit" disabled={busy || (mode === "recovery" && resendCoolingDown)} className="flex h-12 w-full items-center justify-center rounded-xl bg-[#07172d] text-sm font-bold text-white hover:bg-[#0d2748] disabled:opacity-60">
          {busy
            ? <LoaderCircle className="h-4 w-4 animate-spin" />
            : mode === "recovery" && resendCoolingDown
              ? `다시 요청 (${resendCooldown}초)`
              : submitLabel(mode)}
        </button>
      </form>

      <div className="mt-3 flex items-center justify-between text-xs font-semibold text-slate-600">
        {mode === "login" ? <button type="button" onClick={() => changeMode("recovery")} className="underline underline-offset-4">비밀번호 찾기</button> : null}
        {mode === "recovery" || mode === "reset" ? <button type="button" onClick={() => changeMode("login")} className="underline underline-offset-4">로그인으로 돌아가기</button> : null}
        <Link href="/guest" className="ml-auto rounded-lg border border-slate-300 px-3 py-2 hover:bg-slate-50">비회원으로 체험</Link>
      </div>

      {mode === "login" || mode === "signup" ? (
        <section aria-label="간편 로그인" className="mt-6 border-t border-slate-200 pt-5">
          <p className="mb-3 text-center text-xs font-semibold text-slate-500">또는 다른 계정으로 계속하기</p>
          <OAuthLoginPanel />
        </section>
      ) : null}
    </fieldset>
  );
}

function Field({ label, hint, value, onChange, type = "text", ...props }: { label: string; hint?: string; value: string; onChange: (value: string) => void; type?: string; name: string; autoComplete: string }) {
  return (
    <label className="block text-xs font-bold text-slate-700">
      <span className="flex justify-between"><span>{label}</span>{hint ? <span className="font-medium text-slate-400">{hint}</span> : null}</span>
      <input required type={type} value={value} onChange={(event) => onChange(event.target.value)} {...props} className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm font-medium outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100" />
    </label>
  );
}

function Consent({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <label className="flex cursor-pointer items-start gap-2"><input required type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-1 h-4 w-4 rounded border-slate-300" /><span>{label}</span></label>;
}

function submitLabel(mode: Mode) {
  if (mode === "signup") return "이메일 가입 신청";
  if (mode === "recovery") return "재설정 메일 보내기";
  if (mode === "reset") return "새 비밀번호 저장";
  return "이메일로 로그인";
}

function accountErrorMessage(error: unknown) {
  if (error instanceof AuthClientRequestError) return error.message;
  return error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
}

function loginErrorMessage(code: string) {
  const messages: Record<string, string> = {
    oauth_access_denied: "연결 계정 로그인이 취소되었습니다.",
    oauth_code_exchange_failed: "인증 제공자가 로그인 코드를 확인하지 못했습니다. 다시 시도해 주세요.",
    oauth_provider_scope_invalid: "연결 계정의 동의 범위를 확인해 주세요.",
    oauth_not_configured: "연결 계정 로그인이 아직 운영 설정되지 않았습니다.",
    naver_not_configured: "네이버 로그인은 연결 준비 중입니다. 이메일 또는 카카오 로그인을 이용해 주세요.",
  };
  return messages[code] ?? "로그인을 완료하지 못했습니다. 다시 시도해 주세요.";
}

function newPasswordIssue(value: string): string | null {
  if (value.length < 12 || value.length > 128) return "비밀번호는 12자 이상 128자 이하여야 합니다.";
  if (!/[a-z]/.test(value)) return "비밀번호에 영문 소문자를 한 글자 이상 포함해 주세요.";
  if (!/[A-Z]/.test(value)) return "비밀번호에 영문 대문자를 한 글자 이상 포함해 주세요.";
  if (!/[0-9]/.test(value)) return "비밀번호에 숫자를 한 글자 이상 포함해 주세요.";
  if (!/[^A-Za-z0-9]/.test(value)) return "비밀번호에 기호를 한 글자 이상 포함해 주세요.";
  return null;
}
