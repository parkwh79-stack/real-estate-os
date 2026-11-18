"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  ShieldPlus,
  UserRound,
} from "lucide-react";
import {
  changeTenantMembershipRole,
  loadTenantMemberships,
  TenantMembershipRoleChangeError,
  type RealyticsUser,
  type TenantMembership,
  type TenantMembershipRole,
} from "../../lib/auth-client";

const ROLE_OPTIONS: Array<{ value: TenantMembershipRole; label: string }> = [
  { value: "analyst", label: "분석 담당" },
  { value: "reviewer", label: "심사역" },
  { value: "viewer", label: "조회자" },
  { value: "legal_reviewer", label: "법무 검토자" },
  { value: "external_counsel", label: "외부 법률자문" },
  { value: "compliance_officer", label: "준법 담당" },
  { value: "legal_counsel_approver", label: "법무 승인자" },
  { value: "admin", label: "테넌트 관리자" },
];

export function TenantMembershipManagement({
  currentUser,
  assuranceLevel,
}: {
  currentUser: RealyticsUser | null;
  assuranceLevel: "aal1" | "aal2" | null;
}) {
  const [memberships, setMemberships] = useState<TenantMembership[]>([]);
  const [loading, setLoading] = useState(true);
  const [changingKey, setChangingKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshMemberships = useCallback(async (preserveMessages = false) => {
    setLoading(true);
    if (!preserveMessages) {
      setNotice(null);
      setError(null);
    }
    try {
      const result = await loadTenantMemberships();
      setMemberships(result.memberships);
    } catch (caught) {
      setMemberships([]);
      setError(caught instanceof Error ? caught.message : "테넌트 사용자 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshMemberships();
  }, [refreshMemberships]);

  const changeRole = useCallback(async (
    membership: TenantMembership,
    role: TenantMembershipRole,
    reason: string,
  ) => {
    const key = membershipKey(membership);
    setChangingKey(key);
    setNotice(null);
    setError(null);
    try {
      const result = await changeTenantMembershipRole({
        targetIssuer: membership.issuer,
        targetSubject: membership.subject,
        expectedMembershipVersion: membership.membershipVersion,
        role,
        reason,
      });
      await refreshMemberships(true);
      setNotice(`${result.membership.displayName ?? result.membership.email ?? "사용자"}의 역할을 ${roleLabel(result.membership.role)}(으)로 변경했습니다.`);
    } catch (caught) {
      if (
        caught instanceof TenantMembershipRoleChangeError
        && ["membership_version_conflict", "tenant_membership_not_found"].includes(caught.code)
      ) {
        await refreshMemberships(true);
      }
      setError(caught instanceof Error ? caught.message : "테넌트 역할 변경에 실패했습니다.");
    } finally {
      setChangingKey(null);
    }
  }, [refreshMemberships]);

  return (
    <section
      data-testid="tenant-membership-management"
      className="rounded-re-panel border border-re-border bg-re-surface p-5 text-re-text shadow-re-panel"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-2">
          <ShieldPlus className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" aria-hidden="true" />
          <div>
            <h2 className="text-lg font-bold text-re-text">테넌트 사용자 역할 관리</h2>
            <p className="mt-1 text-xs leading-5 text-re-secondary">
              OAuth 제공자나 이메일이 아니라 서버가 검증한 issuer + subject membership을 기준으로 역할을 변경합니다.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void refreshMemberships()}
          disabled={loading || Boolean(changingKey)}
          className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-md border border-re-border px-3 text-xs font-bold text-re-secondary disabled:cursor-wait disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
          사용자 새로고침
        </button>
      </div>

      <div className={`mt-4 flex items-start gap-2 rounded-lg border p-3 text-xs leading-5 ${
        assuranceLevel === "aal2"
          ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200"
          : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200"
      }`}>
        {assuranceLevel === "aal2" ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <p>
          {assuranceLevel === "aal2"
            ? "현재 세션은 AAL2입니다. 서버가 추가 인증 시각을 다시 검증한 뒤 역할을 변경합니다."
            : "역할 변경에는 최근 TOTP AAL2 추가 인증이 필요합니다. 상단 인증 보안에서 현재 세션을 AAL2로 승격해 주세요."}
        </p>
      </div>

      {notice ? (
        <div role="status" className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm font-semibold text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}
      {error ? (
        <div role="alert" data-testid="membership-management-error" className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm font-semibold text-rose-700 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-300">
          {error}
        </div>
      ) : null}

      <div className="mt-4 space-y-3">
        {memberships.length ? memberships.map((membership) => (
          <MembershipEditor
            key={membershipKey(membership)}
            membership={membership}
            current={isCurrentMembership(currentUser, membership)}
            aal2={assuranceLevel === "aal2"}
            busy={changingKey === membershipKey(membership)}
            anyBusy={Boolean(changingKey)}
            onChange={changeRole}
          />
        )) : (
          <div className="rounded-lg border border-dashed border-re-border p-6 text-center text-sm text-re-muted">
            {loading ? "테넌트 membership을 불러오는 중입니다." : "표시할 테넌트 membership이 없습니다."}
          </div>
        )}
      </div>
    </section>
  );
}

function MembershipEditor({
  membership,
  current,
  aal2,
  busy,
  anyBusy,
  onChange,
}: {
  membership: TenantMembership;
  current: boolean;
  aal2: boolean;
  busy: boolean;
  anyBusy: boolean;
  onChange: (
    membership: TenantMembership,
    role: TenantMembershipRole,
    reason: string,
  ) => Promise<void>;
}) {
  const [role, setRole] = useState<TenantMembershipRole>(membership.role);
  const [reason, setReason] = useState("");
  const active = membership.status === "active"
    && membership.principalStatus === "active"
    && membership.principalType === "human";
  const editable = !current && active;
  const submitReady = editable && aal2 && role !== membership.role && reason.trim().length >= 3;
  const name = membership.displayName ?? membership.email ?? "이름 미등록 사용자";

  useEffect(() => {
    setRole(membership.role);
    setReason("");
  }, [membership.membershipVersion, membership.role]);

  return (
    <article
      data-testid={`membership-row-${membership.subject}`}
      className="rounded-xl border border-re-border bg-re-subtle p-4"
    >
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.8fr)] xl:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <UserRound className="h-4 w-4 text-re-muted" aria-hidden="true" />
            <h3 className="text-sm font-black text-re-text">{name}</h3>
            <span className="rounded-md border border-re-border bg-re-surface px-2 py-0.5 text-[10px] font-bold text-re-secondary">
              {roleLabel(membership.role)}
            </span>
            {current ? (
              <span className="rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-black text-blue-700 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-200">
                현재 사용자 · 자기 역할 변경 차단
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-re-secondary">{membership.email ?? "이메일 비공개"}</p>
          <div className="mt-3 rounded-lg border border-re-border bg-re-surface p-3">
            <p className="text-[10px] font-black uppercase tracking-[0.12em] text-re-muted">권한 신원 키 · provider-neutral</p>
            <p className="mt-1 break-all font-mono text-[11px] leading-4 text-re-secondary">{membership.issuer}</p>
            <p className="mt-1 break-all font-mono text-[11px] leading-4 text-re-secondary">{membership.subject}</p>
          </div>
          <p className="mt-2 text-[11px] text-re-muted">
            Principal {membership.principalType}/{membership.principalStatus} · Membership {membership.status} · Version {membership.membershipVersion}
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-semibold text-re-secondary">변경할 역할</span>
            <select
              aria-label={`${name} 변경할 역할`}
              value={role}
              onChange={(event) => setRole(event.target.value as TenantMembershipRole)}
              disabled={!editable || anyBusy}
              className="mt-1 h-10 w-full rounded-lg border border-re-border bg-re-surface px-3 text-sm text-re-text disabled:cursor-not-allowed disabled:opacity-50"
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-re-secondary">변경 사유</span>
            <input
              aria-label={`${name} 역할 변경 사유`}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={!editable || anyBusy}
              minLength={3}
              maxLength={1000}
              placeholder="승인된 업무 배정 사유"
              className="mt-1 h-10 w-full rounded-lg border border-re-border bg-re-surface px-3 text-sm text-re-text disabled:cursor-not-allowed disabled:opacity-50"
            />
          </label>
          <div className="sm:col-span-2">
            <button
              type="button"
              onClick={() => void onChange(membership, role, reason.trim())}
              disabled={!submitReady || anyBusy}
              title={!aal2 ? "최근 TOTP AAL2 추가 인증이 필요합니다." : undefined}
              className="inline-flex h-10 items-center justify-center rounded-lg bg-blue-600 px-4 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "역할 변경 중" : current ? "자기 역할 변경 불가" : !aal2 ? "AAL2 인증 후 역할 변경" : "역할 변경"}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

function membershipKey(membership: TenantMembership) {
  return `${membership.issuer}::${membership.subject}`;
}

function isCurrentMembership(user: RealyticsUser | null, membership: TenantMembership) {
  if (!user) return false;
  const issuer = String(user.metadata?.issuer ?? "").trim().replace(/\/+$/, "");
  const subject = String(user.metadata?.subject ?? "").trim();
  if (issuer && subject) {
    return issuer === membership.issuer.trim().replace(/\/+$/, "")
      && subject === membership.subject.trim();
  }
  return membership.subject.trim() === user.userId.trim();
}

function roleLabel(role: TenantMembershipRole) {
  return ROLE_OPTIONS.find((option) => option.value === role)?.label ?? role;
}
