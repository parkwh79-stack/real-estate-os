"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertCircle, Building2, CheckCircle2, Clock3, KeyRound, LoaderCircle, RefreshCw, Search, Send } from "lucide-react";
import {
  acceptOnboardingInvitationCode,
  acceptPendingOnboardingInvitation,
  AuthClientRequestError,
  loadOnboardingStatus,
  REALYTICS_PRIVACY_VERSION,
  REALYTICS_TERMS_VERSION,
  resubmitOnboardingRequest,
  saveOnboardingProfile,
  searchOnboardingOrganizations,
  selectSupabaseTenant,
  submitOnboardingRequest,
  type OnboardingAccessRequest,
  type OnboardingOrganization,
  type OnboardingStatus,
} from "../../lib/auth-client";
import { useAuth } from "../../components/auth/auth-context";
import { DefaultWorkspaceGate } from "../../components/auth/default-workspace-gate";

export default function OnboardingPage() {
  return <DefaultWorkspaceGate><OrganizationOnboardingPage /></DefaultWorkspaceGate>;
}

function OrganizationOnboardingPage() {
  const {
    status: authStatus,
    user,
    refresh: refreshAuth,
    logout,
  } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [deferredInvitation, setDeferredInvitation] = useState<
    "profile_required" | "policy_update_required" | "authority_unavailable" | null
  >(null);
  const [manualInvitationCode, setManualInvitationCode] = useState("");
  const [organizationQuery, setOrganizationQuery] = useState("");
  const [organizations, setOrganizations] = useState<OnboardingOrganization[]>([]);
  const [selectedOrganization, setSelectedOrganization] = useState<OnboardingOrganization | null>(null);
  const [organizationName, setOrganizationName] = useState("");
  const [businessNumber, setBusinessNumber] = useState("");
  const [requestedRole, setRequestedRole] = useState<"analyst" | "viewer">("analyst");
  const [message, setMessage] = useState("");

  const refreshStatus = useCallback(async (preserveMessage = false) => {
    if (authStatus === "authority_unavailable") {
      setLoading(false);
      return;
    }
    setLoading(true);
    if (!preserveMessage) {
      setNotice(null);
      setError(null);
    }
    try {
      const next = await loadOnboardingStatus();
      setStatus(next);
      if (next.profile) {
        const currentTerms = next.policyVersions?.termsVersion ?? REALYTICS_TERMS_VERSION;
        const currentPrivacy = next.policyVersions?.privacyVersion ?? REALYTICS_PRIVACY_VERSION;
        setDisplayName(next.profile.displayName);
        setPhone(next.profile.phone ?? "");
        setJobTitle(next.profile.jobTitle ?? "");
        setTermsAccepted(next.profile.termsVersion === currentTerms);
        setPrivacyAccepted(next.profile.privacyVersion === currentPrivacy);
      } else {
        setDisplayName((current) => current || user?.displayName || "");
      }
    } catch (caught) {
      setError(onboardingErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [authStatus, user?.displayName]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    const invitation = new URLSearchParams(window.location.search).get("invitation");
    if (
      invitation === "profile_required"
      || invitation === "policy_update_required"
      || invitation === "authority_unavailable"
    ) {
      setDeferredInvitation(invitation);
    }
  }, []);

  const activeRequest = useMemo(
    () => status?.requests.find((request) => request.status === "pending")
      ?? status?.requests.find((request) => request.status === "rejected")
      ?? status?.requests[0]
      ?? null,
    [status?.requests],
  );
  const termsVersion = status?.policyVersions?.termsVersion ?? REALYTICS_TERMS_VERSION;
  const privacyVersion = status?.policyVersions?.privacyVersion ?? REALYTICS_PRIVACY_VERSION;
  const profileCurrent = Boolean(
    status?.profile
    && status.profile.termsVersion === termsVersion
    && status.profile.privacyVersion === privacyVersion,
  );

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!displayName.trim()) return setError("표시명을 입력해 주세요.");
    if (!termsAccepted || !privacyAccepted) return setError("현재 정책 버전을 모두 확인하고 동의해 주세요.");
    setBusy("profile");
    setError(null);
    setNotice(null);
    try {
      await saveOnboardingProfile({
        displayName: displayName.trim(),
        phone: phone.trim() || null,
        jobTitle: jobTitle.trim() || null,
        termsAccepted: true,
        privacyAccepted: true,
        termsVersion,
        privacyVersion,
        expectedProfileVersion: status?.profile?.profileVersion ?? 0,
      });
      try {
        const accepted = await acceptPendingOnboardingInvitation();
        setDeferredInvitation(null);
        setNotice("프로필과 정책 동의를 저장하고 초대를 수락했습니다. 워크스페이스로 이동합니다.");
        refreshAuth();
        window.location.assign(accepted.next);
        return;
      } catch (invitationError) {
        if (
          invitationError instanceof AuthClientRequestError
          && invitationError.code === "invitation_intent_missing"
        ) {
          setDeferredInvitation(null);
          await refreshStatus(true);
          setNotice("프로필과 현재 정책 동의를 안전하게 저장했습니다.");
          return;
        }
        if (
          invitationError instanceof AuthClientRequestError
          && ["terms_version_mismatch", "privacy_version_mismatch", "onboarding_policy_reacceptance_required"].includes(invitationError.code)
        ) {
          setDeferredInvitation("policy_update_required");
          setTermsAccepted(false);
          setPrivacyAccepted(false);
          await refreshStatus(true);
          setError(onboardingErrorMessage(invitationError));
          return;
        }
        if (
          invitationError instanceof AuthClientRequestError
          && invitationError.code === "onboarding_profile_required"
        ) {
          setDeferredInvitation("profile_required");
        } else if (
          invitationError instanceof AuthClientRequestError
          && invitationError.status >= 500
        ) {
          setDeferredInvitation("authority_unavailable");
        } else {
          setDeferredInvitation(null);
        }
        await refreshStatus(true);
        setError(`프로필은 저장되었지만 초대를 완료하지 못했습니다. ${onboardingErrorMessage(invitationError)}`);
        return;
      }
    } catch (caught) {
      if (caught instanceof AuthClientRequestError && ["policy_version_mismatch", "terms_version_mismatch", "privacy_version_mismatch", "onboarding_profile_version_conflict"].includes(caught.code)) {
        setTermsAccepted(false);
        setPrivacyAccepted(false);
        await refreshStatus(true);
      }
      setError(onboardingErrorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function retryDeferredInvitation() {
    setBusy("invitation"); setError(null); setNotice(null);
    try {
      const accepted = await acceptPendingOnboardingInvitation();
      setDeferredInvitation(null);
      setNotice("초대를 수락했습니다. 승인된 워크스페이스로 이동합니다.");
      refreshAuth();
      window.location.assign(accepted.next);
    } catch (caught) {
      if (caught instanceof AuthClientRequestError) {
        if (caught.code === "invitation_intent_missing") {
          setDeferredInvitation(null);
        } else if (caught.code === "onboarding_profile_required") {
          setDeferredInvitation("profile_required");
        } else if (["terms_version_mismatch", "privacy_version_mismatch", "onboarding_policy_reacceptance_required"].includes(caught.code)) {
          setDeferredInvitation("policy_update_required");
          setTermsAccepted(false);
          setPrivacyAccepted(false);
          await refreshStatus(true);
        } else if (caught.status >= 500) {
          setDeferredInvitation("authority_unavailable");
        } else {
          setDeferredInvitation(null);
        }
      }
      setError(onboardingErrorMessage(caught));
    } finally { setBusy(null); }
  }

  async function acceptManualInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = normalizedInvitationCode(manualInvitationCode);
    if (!code) {
      setError("초대 코드 형식을 확인해 주세요. 초대 링크 전체가 아닌 코드 값만 입력해야 합니다.");
      return;
    }
    if (!profileCurrent) {
      setError("초대 코드를 사용하기 전에 프로필과 현재 약관·개인정보 처리방침 동의를 저장해 주세요.");
      return;
    }

    // Remove the one-time code from React state before the network request.
    // A failed attempt must be re-entered rather than retained in the browser.
    setManualInvitationCode("");
    setBusy("manual-invitation");
    setError(null);
    setNotice(null);
    try {
      const accepted = await acceptOnboardingInvitationCode(code);
      setDeferredInvitation(null);
      setNotice("초대 코드를 확인했습니다. 승인된 워크스페이스로 이동합니다.");
      refreshAuth();
      window.location.assign(accepted.next);
    } catch (caught) {
      if (
        caught instanceof AuthClientRequestError
        && [
          "terms_version_mismatch",
          "privacy_version_mismatch",
          "onboarding_policy_reacceptance_required",
        ].includes(caught.code)
      ) {
        setTermsAccepted(false);
        setPrivacyAccepted(false);
        await refreshStatus(true);
      }
      setError(onboardingErrorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function searchOrganizations(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (organizationQuery.trim().length < 3) return setError("기관명 세 글자 이상을 입력해 주세요.");
    setBusy("search"); setError(null);
    try {
      setOrganizations(await searchOnboardingOrganizations(organizationQuery));
    } catch (caught) { setError(onboardingErrorMessage(caught)); }
    finally { setBusy(null); }
  }

  async function submitRequest(type: "existing_organization" | "new_organization") {
    if (!status?.profile) return setError("기관 가입 신청 전에 프로필과 정책 동의를 저장해 주세요.");
    if (type === "existing_organization" && !selectedOrganization) return setError("가입할 기존 기관을 선택해 주세요.");
    if (type === "new_organization" && !organizationName.trim()) return setError("신규 기관명을 입력해 주세요.");
    const normalizedBusinessNumber = normalizeBusinessRegistrationNumber(businessNumber);
    if (type === "new_organization" && normalizedBusinessNumber.length !== 10) {
      return setError("사업자등록번호 10자리를 입력해 주세요. 하이픈은 자동으로 제거됩니다.");
    }
    setBusy(type); setError(null); setNotice(null);
    try {
      await submitOnboardingRequest({
        requestType: type,
        ...(type === "existing_organization"
          ? { organizationId: selectedOrganization!.organizationId }
          : { organizationName: organizationName.trim(), businessRegistrationNumber: normalizedBusinessNumber }),
        ...(type === "existing_organization" ? { requestedRole } : {}),
        message: message.trim() || null,
      });
      await refreshStatus(true);
      setNotice("기관 가입 신청을 제출했습니다. 승인 결과는 이 화면에서 확인할 수 있습니다.");
    } catch (caught) { setError(onboardingErrorMessage(caught)); }
    finally { setBusy(null); }
  }

  async function resubmit(
    request: OnboardingAccessRequest,
    draft: ResubmitDraft,
  ) {
    const normalizedBusinessNumber = normalizeBusinessRegistrationNumber(
      draft.businessRegistrationNumber,
    );
    if (request.requestType === "new_organization" && normalizedBusinessNumber.length !== 10) {
      setError("신규 기관 재신청에는 사업자등록번호 10자리가 필요합니다.");
      return;
    }
    setBusy(`resubmit-${request.requestId}`); setError(null); setNotice(null);
    try {
      await resubmitOnboardingRequest(request.requestId, {
        expectedRequestVersion: request.requestVersion,
        ...(request.requestType === "existing_organization"
          ? { requestedRole: draft.requestedRole }
          : {}),
        message: draft.message.trim() || null,
        ...(request.requestType === "new_organization" ? {
          organizationName: draft.organizationName.trim() || undefined,
          businessRegistrationNumber: normalizedBusinessNumber,
        } : {}),
      });
      await refreshStatus(true);
      setNotice("보완한 가입 신청을 다시 제출했습니다.");
    } catch (caught) {
      if (caught instanceof AuthClientRequestError && ["access_request_version_conflict", "tenant_access_request_version_conflict"].includes(caught.code)) await refreshStatus(true);
      setError(onboardingErrorMessage(caught));
    } finally { setBusy(null); }
  }

  async function selectTenant(tenantId: string) {
    setBusy(`tenant-${tenantId}`);
    setError(null);
    try {
      await selectSupabaseTenant(tenantId);
      refreshAuth();
      window.location.assign("/start");
    } catch (caught) {
      setError(onboardingErrorMessage(caught));
      setBusy(null);
    }
  }

  if (authStatus === "authority_unavailable") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 text-slate-950">
        <section className="w-full max-w-xl rounded-2xl border border-amber-200 bg-white p-7 shadow-sm" data-testid="identity-authority-unavailable">
          <AlertCircle className="h-8 w-8 text-amber-600" />
          <h1 className="mt-4 text-2xl font-black">워크스페이스 권한 정보를 확인할 수 없습니다</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            로그인 세션은 유효하지만 서버 연결 또는 워크스페이스 권한 확인을 완료하지 못했습니다.
            서버가 처음 시작 중이거나 일시적으로 응답하지 않을 수 있습니다. 잠시 후 다시 확인해 주세요.
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            <button type="button" onClick={refreshAuth} className="inline-flex h-10 items-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-bold text-white">
              <RefreshCw className="h-4 w-4" />다시 확인
            </button>
            <button type="button" onClick={() => void logout().then(() => window.location.assign("/login?switch=1"))} className="h-10 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold">
              로그아웃
            </button>
          </div>
        </section>
      </main>
    );
  }

  if (authStatus === "tenant_selection") {
    const memberships = (status?.memberships ?? []).filter(
      (membership) => membership.status === "active"
        && membership.organizationStatus === "active",
    );
    return (
      <main className="min-h-screen bg-slate-50 px-4 py-10 text-slate-950 sm:px-8">
        <div className="mx-auto max-w-3xl">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-blue-700">Tenant selection required</p>
              <h1 className="mt-2 text-3xl font-black tracking-tight">접속할 워크스페이스를 선택하세요</h1>
              <p className="mt-2 text-sm text-slate-600">각 선택은 FastAPI가 현재 로그인 계정의 활성 멤버십으로 다시 검증합니다.</p>
            </div>
            <button type="button" onClick={() => void logout().then(() => window.location.assign("/login?switch=1"))} className="h-10 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold">다른 계정</button>
          </header>
          {error ? <p role="alert" className="mt-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</p> : null}
          {loading ? <p aria-live="polite" className="mt-6 flex items-center gap-2 text-sm text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin" />멤버십 확인 중</p> : null}
          {!loading && memberships.length === 0 ? (
            <p className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm leading-6 text-amber-900">선택할 수 있는 활성 멤버십이 없습니다. 관리자에게 멤버십 상태를 확인해 달라고 요청해 주세요.</p>
          ) : null}
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {memberships.map((membership) => (
              <button
                key={membership.organizationId}
                type="button"
                data-testid={`tenant-option-${membership.organizationId}`}
                disabled={busy !== null}
                onClick={() => void selectTenant(membership.organizationId)}
                className="rounded-2xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-blue-500 disabled:opacity-50"
              >
                <span className="block text-base font-black">{membership.organizationName}</span>
                <span className="mt-2 block text-xs font-semibold text-slate-600">역할 {membership.role} · 멤버십 v{membership.membershipVersion}</span>
                <span className="mt-2 block break-all font-mono text-[11px] text-slate-400">{membership.organizationId}</span>
                <span className="mt-4 inline-flex h-8 items-center rounded-lg bg-blue-700 px-3 text-xs font-bold text-white">
                  {busy === `tenant-${membership.organizationId}` ? "검증 중" : "이 워크스페이스 열기"}
                </span>
              </button>
            ))}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-950 sm:px-8">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div><p className="text-xs font-bold uppercase tracking-[0.18em] text-blue-700">Verified principal onboarding</p><h1 className="mt-2 text-3xl font-black tracking-tight">기관 워크스페이스 가입</h1><p className="mt-2 text-sm text-slate-600">인증 계정: {user?.email ?? user?.userId ?? "확인 중"}</p></div>
          <div className="flex gap-2"><button type="button" onClick={() => void refreshStatus()} className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold"><RefreshCw className="h-4 w-4" />새로고침</button><button type="button" onClick={() => void logout().then(() => window.location.assign("/login?switch=1"))} className="h-10 rounded-xl border border-slate-300 bg-white px-4 text-sm font-bold">다른 계정</button></div>
        </header>

        {error ? <p role="alert" className="mt-5 flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-5 w-5 shrink-0" />{error}</p> : null}
        {notice ? <p role="status" className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">{notice}</p> : null}
        {loading ? <p aria-live="polite" className="mt-5 flex items-center gap-2 text-sm text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin" />가입 상태 확인 중</p> : null}

        {status?.requests.map((request) => (
          <RequestStatusCard
            key={`${request.requestId}-${request.requestVersion}`}
            request={request}
            onResubmit={resubmit}
            busy={busy === `resubmit-${request.requestId}`}
          />
        ))}

        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <SectionTitle icon={CheckCircle2} title="1. 프로필·필수 동의" description="동의는 현재 정책 버전과 함께 PostgreSQL에 기록됩니다." />
            <form onSubmit={saveProfile} className="mt-5 space-y-3"><Input label="표시명" value={displayName} onChange={setDisplayName} required /><div className="grid gap-3 sm:grid-cols-2"><Input label="연락처(선택)" value={phone} onChange={setPhone} /><Input label="직책(선택)" value={jobTitle} onChange={setJobTitle} /></div><Consent checked={termsAccepted} onChange={setTermsAccepted} label={`[필수] 이용약관 ${termsVersion}`} /><Consent checked={privacyAccepted} onChange={setPrivacyAccepted} label={`[필수] 개인정보 처리방침 ${privacyVersion}`} /><button disabled={busy !== null || !termsAccepted || !privacyAccepted} className="h-11 w-full rounded-xl bg-slate-950 text-sm font-bold text-white disabled:opacity-50">{busy === "profile" ? "저장 중" : status?.profile ? "프로필·동의 갱신" : "프로필·동의 저장"}</button></form>
          </section>

          <section data-testid="deferred-invitation" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <SectionTitle icon={KeyRound} title="2. 보류 중인 초대" description="초대 링크의 일회성 토큰은 HttpOnly 쿠키에만 보관되며 화면이나 브라우저 저장소로 다시 노출되지 않습니다." />
            <div className={`mt-5 rounded-xl border p-4 text-sm leading-6 ${deferredInvitation ? "border-amber-200 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
              {deferredInvitation === "profile_required"
                ? "초대가 안전하게 보류되어 있습니다. 프로필과 현재 정책 동의를 저장하면 자동으로 수락을 다시 시도합니다."
                : deferredInvitation === "policy_update_required"
                  ? "정책 개정으로 초대가 보류되어 있습니다. 최신 약관과 개인정보 처리방침에 동의한 뒤 저장해 주세요."
                  : deferredInvitation === "authority_unavailable"
                    ? "초대 권한 서비스를 일시적으로 확인하지 못했습니다. 초대는 삭제되지 않았으며 아래에서 다시 시도할 수 있습니다."
                    : "관리자가 보낸 초대 링크를 열면 토큰이 서버 전용 쿠키로 격리됩니다. 이미 프로필 동의를 마쳤다면 아래에서 보류된 초대를 확인할 수 있습니다."}
            </div>
            <button
              type="button"
              data-testid="accept-deferred-invitation"
              onClick={() => void retryDeferredInvitation()}
              disabled={busy !== null || !profileCurrent}
              className="mt-3 h-11 w-full rounded-xl bg-blue-700 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy === "invitation" ? "초대 확인 중" : profileCurrent ? "보류 중인 초대 수락" : "프로필·현재 정책 동의를 먼저 저장하세요"}
            </button>
            <div className="my-5 flex items-center gap-3 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">
              <span className="h-px flex-1 bg-slate-200" />
              또는
              <span className="h-px flex-1 bg-slate-200" />
            </div>
            <form onSubmit={acceptManualInvitation} className="space-y-3" data-testid="manual-invitation-form">
              <div>
                <label htmlFor="manual-invitation-code" className="text-sm font-bold text-slate-900">
                  초대 코드 직접 입력
                </label>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  코드는 이 요청에서만 사용되며 URL·브라우저 저장소·로그에 보관하지 않습니다.
                  초대 링크 전체가 아닌 코드 값만 입력하세요.
                </p>
              </div>
              <input
                id="manual-invitation-code"
                data-testid="manual-invitation-code"
                type="password"
                autoComplete="one-time-code"
                autoCapitalize="none"
                spellCheck={false}
                minLength={32}
                maxLength={512}
                value={manualInvitationCode}
                onChange={(event) => setManualInvitationCode(event.target.value)}
                disabled={busy !== null || !profileCurrent}
                required
                className="h-11 w-full rounded-xl border border-slate-300 px-3 font-mono text-sm tracking-wide disabled:cursor-not-allowed disabled:bg-slate-100"
              />
              <button
                type="submit"
                data-testid="accept-manual-invitation"
                disabled={busy !== null || !profileCurrent || !manualInvitationCode.trim()}
                className="h-11 w-full rounded-xl border border-blue-700 bg-white text-sm font-bold text-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "manual-invitation"
                  ? "초대 코드 확인 중"
                  : profileCurrent
                    ? "초대 코드 확인"
                    : "프로필·현재 정책 동의를 먼저 저장하세요"}
              </button>
            </form>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <SectionTitle icon={Search} title="3A. 기존 기관 가입 신청" description="기관을 검색한 뒤 분석 담당 또는 조회자 역할을 요청합니다." />
            <form onSubmit={searchOrganizations} className="mt-5 flex gap-2"><input value={organizationQuery} onChange={(event) => setOrganizationQuery(event.target.value)} aria-label="기관 검색" placeholder="기관명 3자 이상 검색" className="h-11 min-w-0 flex-1 rounded-xl border border-slate-300 px-3 text-sm" /><button disabled={busy !== null} className="rounded-xl border border-slate-300 px-4 text-sm font-bold">검색</button></form>
            <div className="mt-3 max-h-40 space-y-2 overflow-auto">{organizations.map((organization) => <button type="button" key={organization.organizationId} onClick={() => setSelectedOrganization(organization)} className={`w-full rounded-xl border p-3 text-left text-sm ${selectedOrganization?.organizationId === organization.organizationId ? "border-blue-600 bg-blue-50" : "border-slate-200"}`}><span className="font-bold">{organization.name}</span><span className="mt-1 block font-mono text-[11px] text-slate-500">{organization.organizationId}</span></button>)}</div>
            <RequestOptions role={requestedRole} setRole={setRequestedRole} message={message} setMessage={setMessage} />
            <button type="button" onClick={() => void submitRequest("existing_organization")} disabled={busy !== null || Boolean(activeRequest?.status === "pending")} className="mt-3 h-11 w-full rounded-xl bg-blue-700 text-sm font-bold text-white disabled:opacity-50">선택 기관에 가입 신청</button>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <SectionTitle icon={Building2} title="3B. 신규 기관 신청" description="등록할 기관이 없으면 플랫폼 운영자의 검토를 요청합니다." />
            <div className="mt-5 space-y-3"><Input label="기관명" value={organizationName} onChange={setOrganizationName} /><BusinessRegistrationNumberInput value={businessNumber} onChange={setBusinessNumber} /><div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs leading-5 text-violet-800"><strong>승인 시 최초 기관 관리자</strong><br />신규 기관은 고아 Tenant가 생기지 않도록 승인과 동시에 신청자에게 관리자 멤버십이 발급됩니다. 신청자가 관리자 역할을 별도로 요청하는 구조는 사용하지 않습니다.</div><RequestOptions role={requestedRole} setRole={setRequestedRole} message={message} setMessage={setMessage} showRole={false} /><button type="button" onClick={() => void submitRequest("new_organization")} disabled={busy !== null || Boolean(activeRequest?.status === "pending")} className="h-11 w-full rounded-xl bg-slate-950 text-sm font-bold text-white disabled:opacity-50">신규 기관 검토 요청</button></div>
          </section>
        </div>
        <p className="mt-6 text-center text-xs leading-5 text-slate-500">OAuth 이메일이나 표시명만으로 기관에 자동 가입되지 않습니다. 승인 전에는 Tenant 데이터·프로젝트·문서 API에 접근할 수 없습니다. <Link href="/guest" className="font-bold underline">비회원 샘플 보기</Link></p>
      </div>
    </main>
  );
}

type ResubmitDraft = {
  requestedRole: "analyst" | "viewer";
  message: string;
  organizationName: string;
  businessRegistrationNumber: string;
};

function RequestStatusCard({ request, onResubmit, busy }: { request: OnboardingAccessRequest; onResubmit: (request: OnboardingAccessRequest, draft: ResubmitDraft) => Promise<void>; busy: boolean }) {
  const presentation = request.status === "approved" ? { icon: CheckCircle2, title: "가입 승인 완료", style: "border-emerald-200 bg-emerald-50 text-emerald-900" } : request.status === "rejected" ? { icon: AlertCircle, title: "보완 후 다시 신청할 수 있습니다", style: "border-rose-200 bg-rose-50 text-rose-900" } : { icon: Clock3, title: "기관 승인 대기 중", style: "border-amber-200 bg-amber-50 text-amber-900" };
  const Icon = presentation.icon;
  return <section data-testid="onboarding-request-status" className={`mt-6 rounded-2xl border p-5 ${presentation.style}`}><div className="flex items-start gap-3"><Icon className="mt-0.5 h-5 w-5 shrink-0" /><div className="min-w-0"><h2 className="font-black">{presentation.title}</h2><p className="mt-1 text-sm">{request.organizationName ?? request.organizationId ?? "기관"} · {request.requestType === "new_organization" ? "승인 시 최초 기관 관리자" : `신청 역할 ${request.requestedRole}`} · 버전 {request.requestVersion}</p>{request.decisionReason ? <p className="mt-2 rounded-lg bg-white/60 p-3 text-sm"><strong>검토 사유:</strong> {request.decisionReason}</p> : null}</div></div>{request.status === "rejected" ? <RejectedRequestForm request={request} busy={busy} onResubmit={onResubmit} /> : null}{request.status === "approved" ? <Link href="/start" className="mt-4 inline-flex h-10 items-center rounded-xl bg-emerald-800 px-4 text-sm font-bold text-white">워크스페이스 열기</Link> : null}</section>;
}

function RejectedRequestForm({ request, busy, onResubmit }: { request: OnboardingAccessRequest; busy: boolean; onResubmit: (request: OnboardingAccessRequest, draft: ResubmitDraft) => Promise<void> }) {
  const [requestedRole, setRequestedRole] = useState<"analyst" | "viewer">(request.requestedRole);
  const [message, setMessage] = useState(request.message ?? "");
  const [organizationName, setOrganizationName] = useState(request.organizationName ?? "");
  const [businessRegistrationNumber, setBusinessRegistrationNumber] = useState(request.businessRegistrationNumber ?? "");
  return (
    <div className="mt-4 rounded-xl border border-rose-200 bg-white/70 p-4" data-testid={`resubmit-form-${request.requestId}`}>
      <p className="text-xs font-bold text-rose-900">이 신청의 보완값</p>
      <p className="mt-1 text-xs leading-5 text-rose-800">다른 신규 신청 입력값과 분리되어 있으며, 거절된 신청의 저장 스냅샷에서 시작합니다.</p>
      {request.requestType === "new_organization" ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Input label="기관명" value={organizationName} onChange={setOrganizationName} required />
          <BusinessRegistrationNumberInput value={businessRegistrationNumber} onChange={setBusinessRegistrationNumber} />
        </div>
      ) : null}
      <RequestOptions
        role={requestedRole}
        setRole={setRequestedRole}
        message={message}
        setMessage={setMessage}
        showRole={request.requestType === "existing_organization"}
      />
      <button
        type="button"
        onClick={() => void onResubmit(request, {
          requestedRole,
          message,
          organizationName,
          businessRegistrationNumber,
        })}
        disabled={busy}
        className="mt-3 inline-flex h-10 items-center gap-2 rounded-xl bg-rose-700 px-4 text-sm font-bold text-white disabled:opacity-50"
      >
        <Send className="h-4 w-4" />{busy ? "재제출 중" : "이 보완값으로 재제출"}
      </button>
    </div>
  );
}

function SectionTitle({ icon: Icon, title, description }: { icon: typeof Building2; title: string; description: string }) { return <div className="flex gap-3"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100"><Icon className="h-5 w-5" /></span><div><h2 className="font-black">{title}</h2><p className="mt-1 text-xs leading-5 text-slate-500">{description}</p></div></div>; }
function Input({ label, value, onChange, required = false }: { label: string; value: string; onChange: (value: string) => void; required?: boolean }) { return <label className="block text-xs font-bold text-slate-700">{label}<input required={required} value={value} onChange={(event) => onChange(event.target.value)} className="mt-1.5 h-11 w-full rounded-xl border border-slate-300 px-3 text-sm" /></label>; }
function Consent({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) { return <label className="flex items-start gap-2 text-xs font-semibold text-slate-700"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-0.5 h-4 w-4" />{label}</label>; }
function RequestOptions({ role, setRole, message, setMessage, showRole = true }: { role: "analyst" | "viewer"; setRole: (role: "analyst" | "viewer") => void; message: string; setMessage: (value: string) => void; showRole?: boolean }) { return <div className={`mt-3 grid gap-3 ${showRole ? "sm:grid-cols-[140px_1fr]" : "grid-cols-1"}`}>{showRole ? <label className="text-xs font-bold text-slate-700">요청 역할<select value={role} onChange={(event) => setRole(event.target.value as "analyst" | "viewer")} className="mt-1.5 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm"><option value="analyst">분석 담당</option><option value="viewer">조회자</option></select></label> : null}<label className="text-xs font-bold text-slate-700">신청 메시지(선택)<input value={message} onChange={(event) => setMessage(event.target.value)} className="mt-1.5 h-11 w-full rounded-xl border border-slate-300 px-3 text-sm" /></label></div>; }
function BusinessRegistrationNumberInput({ value, onChange }: { value: string; onChange: (value: string) => void }) { return <label className="block text-xs font-bold text-slate-700">사업자등록번호(필수)<input required inputMode="numeric" autoComplete="off" placeholder="123-45-67890" value={value} onChange={(event) => onChange(event.target.value.replace(/[^\d-]/g, "").slice(0, 12))} className="mt-1.5 h-11 w-full rounded-xl border border-slate-300 px-3 text-sm" /><span className="mt-1 block font-medium text-slate-500">하이픈을 포함해 입력할 수 있으며 서버 전송 전 숫자 10자리로 정규화됩니다.</span></label>; }
function normalizeBusinessRegistrationNumber(value: string) { return value.replace(/\D/g, ""); }
function normalizedInvitationCode(value: string) {
  const code = value.trim();
  return /^[A-Za-z0-9_-]{32,512}$/.test(code) ? code : null;
}
function onboardingErrorMessage(error: unknown) { if (error instanceof AuthClientRequestError) return error.message; return error instanceof Error ? error.message : "온보딩 요청을 처리하지 못했습니다."; }
