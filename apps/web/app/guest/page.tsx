import Link from "next/link";
import { ArrowRight, BarChart3, FileSearch, Landmark, LockKeyhole, ShieldCheck } from "lucide-react";

const samples = [
  { label: "샘플 사업수지", value: "총사업비 2,450억원", detail: "승인된 예시 데이터의 구성만 둘러봅니다.", icon: BarChart3 },
  { label: "샘플 PF 구조", value: "Senior · Mezzanine · Junior", detail: "트랜치별 상태와 근거 연결 방식을 확인합니다.", icon: Landmark },
  { label: "샘플 문서 검토", value: "Locator · Conflict · Approval", detail: "문서에서 승인까지 이어지는 화면 흐름을 체험합니다.", icon: FileSearch },
];

export default function GuestPage() {
  return (
    <main className="min-h-screen bg-[#07172d] px-5 py-10 text-white sm:px-8 lg:px-12">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-sm font-black text-[#07172d]">RY</span><div><p className="text-sm font-bold tracking-[0.14em]">REALYTICS</p><p className="text-[11px] text-slate-400">GUEST WALKTHROUGH</p></div></div>
          <div className="flex gap-2"><Link href="/login" className="rounded-xl border border-white/20 px-4 py-2 text-sm font-bold hover:bg-white/10">로그인</Link><Link href="/login?mode=signup" className="rounded-xl bg-sky-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-sky-300">가입 신청</Link></div>
        </header>

        <section className="mt-14 rounded-3xl border border-sky-300/20 bg-white/[0.055] p-7 backdrop-blur sm:p-10">
          <div data-testid="guest-sample-only" className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-300/10 px-3 py-1 text-xs font-bold text-amber-200"><ShieldCheck className="h-4 w-4" />비회원 체험 · 샘플 데이터 전용</div>
          <h1 className="mt-5 max-w-3xl text-3xl font-black tracking-tight sm:text-5xl">기관 부동산 심사 흐름을<br />데이터 접근 없이 둘러보세요</h1>
          <p className="mt-5 max-w-2xl text-sm leading-7 text-slate-300 sm:text-base">이 화면은 제품 구조를 설명하기 위한 공개 체험입니다. 실제 프로젝트·Tenant·문서 API를 호출하지 않으며 사용자의 파일도 받지 않습니다.</p>
        </section>

        <section className="mt-6 grid gap-4 md:grid-cols-3">
          {samples.map(({ label, value, detail, icon: Icon }) => <article key={label} className="rounded-2xl border border-white/10 bg-white/[0.045] p-6"><Icon className="h-6 w-6 text-sky-300" /><p className="mt-5 text-xs font-bold uppercase tracking-wider text-slate-400">{label}</p><h2 className="mt-2 text-lg font-black">{value}</h2><p className="mt-3 text-sm leading-6 text-slate-400">{detail}</p><button type="button" disabled aria-disabled="true" className="mt-5 inline-flex items-center gap-2 text-xs font-bold text-slate-500">샘플 상세는 로그인 후 제공 <ArrowRight className="h-3.5 w-3.5" /></button></article>)}
        </section>

        <section className="mt-6 flex flex-col gap-5 rounded-2xl border border-rose-300/20 bg-rose-300/[0.06] p-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex gap-4"><LockKeyhole className="mt-1 h-6 w-6 shrink-0 text-rose-200" /><div><h2 className="font-black">회원 전용 기능은 잠겨 있습니다</h2><p className="mt-1 text-sm leading-6 text-slate-300">실제 문서 업로드, 프로젝트 생성, 분석 실행, Tenant 데이터 조회는 인증과 멤버십 승인 후에만 가능합니다.</p></div></div>
          <button type="button" disabled data-testid="guest-upload-disabled" className="shrink-0 rounded-xl border border-white/15 px-4 py-3 text-sm font-bold text-slate-500">문서 업로드 (회원 전용)</button>
        </section>
      </div>
    </main>
  );
}
