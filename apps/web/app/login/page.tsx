"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  Building2,
  FileCheck2,
  Landmark,
  LockKeyhole,
  Scale,
} from "lucide-react";
import { AccountAccessPanel } from "../../components/auth/account-access-panel";
import { useAuth } from "../../components/auth/auth-context";
import { safeInternalNextPath } from "../../lib/safe-navigation";

export default function LoginPage() {
  const router = useRouter();
  const { status } = useAuth();

  useEffect(() => {
    // Recovery creates an authenticated Supabase session before this page.
    // Keep its form visible; the POST still requires the server recovery cookie.
    const query = new URLSearchParams(window.location.search);
    if (query.get("mode") === "reset" || query.get("flow") === "password_recovery") return;
    if (status === "authenticated") router.replace(resolveNextPath());
    if (
      status === "onboarding"
      || status === "tenant_selection"
      || status === "authority_unavailable"
    ) {
      router.replace("/onboarding");
    }
  }, [router, status]);

  return (
    <main className="min-h-screen bg-white text-slate-950">
      <div className="grid min-h-screen lg:grid-cols-[minmax(0,1.18fr)_minmax(420px,0.82fr)]">
        <section className="relative hidden min-h-screen overflow-hidden bg-[#07172d] px-10 py-12 text-white lg:flex lg:flex-col lg:justify-between xl:px-16 xl:py-14">
          <div
            aria-hidden="true"
            className="absolute inset-0 opacity-70"
            style={{
              backgroundImage:
                "radial-gradient(circle at 76% 30%, rgba(58,132,255,0.24), transparent 30%), radial-gradient(circle at 8% 92%, rgba(10,175,149,0.15), transparent 34%), linear-gradient(rgba(126,166,218,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(126,166,218,0.07) 1px, transparent 1px)",
              backgroundSize: "auto, auto, 48px 48px, 48px 48px",
            }}
          />
          <div
            aria-hidden="true"
            className="absolute -bottom-44 -right-36 h-[620px] w-[620px] rounded-full border border-sky-300/20"
          >
            <div className="absolute inset-20 rounded-full border border-sky-300/15" />
            <div className="absolute inset-40 rounded-full border border-sky-300/10" />
          </div>

          <div className="relative z-10">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-sm font-black text-[#07172d]">
                RY
              </span>
              <div>
                <p className="text-sm font-bold tracking-[0.14em]">REALYTICS</p>
                <p className="text-[11px] tracking-[0.16em] text-slate-400">
                  REAL ESTATE DECISION OS
                </p>
              </div>
            </div>

            <div className="mt-20 max-w-2xl xl:mt-24">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-300">
                Institutional real estate workflow
              </p>
              <h1 className="mt-5 text-4xl font-black leading-[1.22] tracking-[-0.045em] xl:text-[3.35rem]">
                기관의 부동산 투자 의사결정을
                <br />
                더 빠르고 정확하게
              </h1>
              <p className="mt-6 max-w-xl text-base leading-7 text-slate-300 xl:text-lg xl:leading-8">
                문서 근거, 사업성 분석, PF 실행과 승인 조건을 하나의 검토 흐름으로
                연결합니다. 핵심 수치와 판단 상태는 원문 근거와 함께 추적됩니다.
              </p>
            </div>

            <div className="mt-11 grid max-w-2xl gap-3 xl:mt-14 xl:grid-cols-3">
              <ProductPrinciple
                icon={<FileCheck2 className="h-5 w-5" />}
                title="근거 중심 검토"
                description="문서·표·셀 위치와 검토 이력을 함께 확인"
              />
              <ProductPrinciple
                icon={<Landmark className="h-5 w-5" />}
                title="금융 실행 연결"
                description="사업수지부터 Debt·Gate까지 동일 프로젝트 기준"
              />
              <ProductPrinciple
                icon={<Scale className="h-5 w-5" />}
                title="승인 책임 분리"
                description="실행·검토·승인 상태를 서로 다른 축으로 관리"
              />
            </div>
          </div>

          <div className="relative z-10 flex items-center justify-between border-t border-white/10 pt-6 text-xs text-slate-400">
            <span>Tenant membership · RBAC · Audit trail</span>
            <span className="inline-flex items-center gap-2 text-slate-300">
              <LockKeyhole className="h-4 w-4 text-sky-300" />
              초대 또는 관리자 승인 후 접근
            </span>
          </div>
        </section>

        <section className="relative flex min-h-screen items-center justify-center bg-[#fbfcfe] px-5 py-10 sm:px-10 lg:px-12 xl:px-20">
          <div
            aria-hidden="true"
            className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent lg:hidden"
          />
          <div className="w-full max-w-[470px]">
            <div className="mb-9 flex items-center gap-3 lg:hidden">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#07172d] text-sm font-black text-white">
                RY
              </span>
              <div>
                <p className="text-sm font-bold tracking-[0.12em]">REALYTICS</p>
                <p className="text-[11px] text-slate-500">REAL ESTATE DECISION OS</p>
              </div>
            </div>

            <div className="mb-8">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 shadow-sm">
                <Building2 className="h-5 w-5" />
              </div>
              <h2
                data-testid="login-heading"
                className="mt-5 text-3xl font-black tracking-[-0.035em] text-slate-950"
              >
                워크스페이스 로그인
              </h2>
              <p className="mt-3 text-sm leading-6 text-slate-600">
                조직에서 승인한 계정으로 로그인하세요. 인증 후 PostgreSQL의
                테넌트 멤버십과 역할을 다시 확인합니다.
              </p>
            </div>

            <AccountAccessPanel />

            <p className="mt-7 text-sm leading-6 text-slate-600">
              아직 소속 기관이 없다면 이메일 확인 후 신규 기관 신청을 제출할 수 있습니다.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}

function ProductPrinciple({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.055] p-4 backdrop-blur-sm">
      <div className="text-sky-300">{icon}</div>
      <p className="mt-3 text-sm font-bold text-white">{title}</p>
      <p className="mt-1.5 text-xs leading-5 text-slate-400">{description}</p>
    </div>
  );
}

function resolveNextPath() {
  if (typeof window === "undefined") return "/start";
  const next = new URLSearchParams(window.location.search).get("next");
  return safeInternalNextPath(next);
}
