import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, CheckCircle2, FileText, ListChecks, Sparkles } from "lucide-react";
import { REAL_ESTATE_TEMPLATES } from "../../lib/real-estate-templates";

export default function TemplatesPage() {
  const categories = Array.from(new Set(REAL_ESTATE_TEMPLATES.map((template) => template.category)));

  return (
    <div className="space-y-6 pb-8">
      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-bold text-indigo-600 dark:text-indigo-300">
              <Sparkles className="h-4 w-4" />
              REALYTICS 업무 템플릿
            </div>
            <h2 className="mt-3 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              부동산 개발·PF 심사 템플릿 갤러리
            </h2>
            <p className="mt-3 max-w-3xl text-sm font-semibold leading-6 text-slate-500">
              사업 유형별로 필요한 입력자료, 실행할 Agent workflow, 검증조건, 최종 산출물을 미리 정리했습니다.
              파일럿에서는 템플릿을 기준으로 사업자료를 준비한 뒤 프로젝트의 AI 업무 주문서에서 실행합니다.
            </p>
          </div>
          <Link
            href="/dashboard"
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950"
          >
            프로젝트로 이동
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {categories.map((category) => (
            <span
              key={category}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-bold text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300"
            >
              {category}
            </span>
          ))}
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        {REAL_ESTATE_TEMPLATES.map((template) => {
          const Icon = template.icon;
          return (
            <article
              key={template.key}
              className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                  <Icon className="h-6 w-6" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-black text-slate-950 dark:text-white">{template.title}</h3>
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-black text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-200">
                      {template.category}
                    </span>
                  </div>
                  <p className="mt-1 text-sm font-bold text-slate-600 dark:text-slate-300">{template.subtitle}</p>
                  <p className="mt-3 text-sm leading-6 text-slate-500">{template.purpose}</p>
                </div>
              </div>

              <div className="mt-5 grid gap-3 lg:grid-cols-2">
                <TemplateBox title="필요 입력자료" icon={<FileText className="h-4 w-4" />} items={template.requiredMaterials} />
                <TemplateBox title="실행 workflow" icon={<ListChecks className="h-4 w-4" />} items={template.workflows} />
                <TemplateBox title="최종 산출물" icon={<CheckCircle2 className="h-4 w-4" />} items={template.outputs} />
                <TemplateBox title="검증조건" icon={<CheckCircle2 className="h-4 w-4" />} items={template.validationChecks} />
              </div>

              <div className="mt-4 rounded-lg bg-slate-50 p-3 dark:bg-slate-950/40">
                <p className="text-xs font-bold uppercase text-slate-500">추천 적용 대상</p>
                <p className="mt-2 text-sm font-semibold leading-6 text-slate-700 dark:text-slate-200">
                  {template.recommendedFor.join(", ")}
                </p>
              </div>
            </article>
          );
        })}
      </section>
    </div>
  );
}

function TemplateBox({ title, icon, items }: { title: string; icon: ReactNode; items: string[] }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-center gap-2 text-sm font-bold text-slate-950 dark:text-white">
        {icon}
        {title}
      </div>
      <ul className="mt-2 space-y-1 text-xs font-semibold leading-5 text-slate-500">
        {items.slice(0, 5).map((item) => (
          <li key={item}>- {item}</li>
        ))}
      </ul>
    </div>
  );
}
