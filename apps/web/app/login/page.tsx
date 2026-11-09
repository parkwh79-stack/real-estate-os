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
                부동산 검토와 분석
              </p>
              <h1 className="mt-5 text-4xl font-black leading-[1.22] tracking-[-0.045em] xl:text-[3.35rem]">
                문서와 숫자를 연결해
                <br />
                부동산 투자를 검토하세요
              </h1>
              <p className="mt-6 max-w-xl text-base leading-7 text-slate-300 xl:text-lg xl:leading-8">
                문서 근거와 사업성 분석을 한곳에서 확인하세요. 프로젝트의 핵심 수치와
                검토 상태를 원문 근거와 함께 살펴볼 수 있습니다.
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
                title="자료와 분석 연결"
                description="사업수지·대출 조건·검토 결과를 함께 확인"
              />
              <ProductPrinciple
                icon={<Scale className="h-5 w-5" />}
                title="검토 상태 구분"
                description="작성·검토·승인 상태를 구분해 확인"
              />
            </div>
          </div>

          <div className="relative z-10 flex items-center justify-between border-t border-white/10 pt-6 text-xs text-slate-400">
            <span>문서 근거 · 작업 이력 · 계정별 접근</span>
            <span className="inline-flex items-center gap-2 text-slate-300">
              <LockKeyhole className="h-4 w-4 text-sky-300" />
              로그인 후 사용할 작업공간 확인
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
                로그인하고 시작하기
              </h2>
              <p className="mt-3 text-sm leading-6 text-slate-600">
                이메일이나 연결된 계정으로 로그인해 주세요. 로그인 후 사용할
                작업공간을 확인합니다.
              </p>
            </div>

            <AccountAccessPanel />

            <p className="mt-7 text-sm leading-6 text-slate-600">
              이메일로 가입하면 메일의 확인 링크를 누른 뒤 안내에 따라 작업공간으로 이동해 주세요.
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
