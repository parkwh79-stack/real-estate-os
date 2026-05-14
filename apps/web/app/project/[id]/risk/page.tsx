import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  BadgeCheck,
  Building2,
  CheckCircle2,
  CircleDot,
  FileWarning,
  ShieldCheck,
  TrendingDown,
} from "lucide-react";

interface RiskItem {
  category?: string;
  item?: string;
  description?: string;
  severity?: number;
  probability?: number;
  score?: number;
  grade?: string;
  mitigation?: string;
  residual_risk?: string;
  opinion?: string;
}

interface RiskCategoryRow {
  category?: string;
  item_count?: number;
  average_score?: number;
  max_score?: number;
  grade?: string;
  critical_count?: number;
  top_item?: string;
}

interface RiskMatrixResponse {
  matrixType?: string;
  overall?: {
    average_score?: number;
    overall_grade?: string;
    critical_count?: number;
    critical_items?: string[];
  };
  categories?: RiskCategoryRow[];
  items?: RiskItem[];
  topRisks?: RiskItem[];
  recommendations?: string[];
  missingCategories?: string[];
}

const apiBase = "http://127.0.0.1:8000";

const riskControls = [
  { category: "Land Risk", item: "토지소유권 확보", owner: "토지/법무", evidence: "매매계약서, 등기부, 신탁계약" },
  { category: "Land Risk", item: "지분합계 및 공유자 동의", owner: "토지/법무", evidence: "토지조서, 공유자 동의서" },
  { category: "Land Risk", item: "제한물권·압류·가처분 해소", owner: "법무", evidence: "등기부등본, 말소확약" },
  { category: "Land Risk", item: "국공유지·특수필지 처리", owner: "인허가/토지", evidence: "국공유지 매입 협의자료" },
  { category: "Permit Risk", item: "인허가 일정 및 승인 가능성", owner: "인허가", evidence: "인허가 로드맵, 사전협의 기록" },
  { category: "Permit Risk", item: "용도지역·건폐율·용적률 적합성", owner: "설계/인허가", evidence: "토지이용계획, 건축개요" },
  { category: "Developer Risk", item: "시행사 재무 및 신용도", owner: "심사", evidence: "재무제표, 신용평가" },
  { category: "Contractor Risk", item: "시공사 책임준공 및 신용도", owner: "심사/기술", evidence: "도급계약, 책임준공 확약" },
  { category: "Construction Cost Risk", item: "공사비 증액 및 원가 검증", owner: "기술/심사", evidence: "공사비 견적, 원가검토서" },
  { category: "Market Risk", item: "입지 및 경쟁단지", owner: "분양성", evidence: "시장조사, 경쟁단지표" },
  { category: "Sales Risk", item: "분양가 적정성", owner: "분양성", evidence: "분양가 비교표, 감정/시세자료" },
  { category: "Sales Risk", item: "분양률 Trigger 및 할인분양", owner: "심사/분양", evidence: "할인분양 trigger table" },
  { category: "Financing Risk", item: "PF 조달구조 및 Tranche", owner: "금융", evidence: "금융조건표, 대주단 term sheet" },
  { category: "Financing Risk", item: "LTV·LTC·DSCR 기준", owner: "심사", evidence: "PF metrics 산출표" },
  { category: "Financing Risk", item: "금리·수수료·All-in Cost", owner: "금융", evidence: "대출조건, 수수료 산정표" },
  { category: "Exit Risk", item: "현금흐름 및 Max Minus", owner: "사업성", evidence: "월별 CF, 자금수지표" },
  { category: "Trust Risk", item: "자금집행 순서 및 변제순위", owner: "신탁/금융", evidence: "자금관리계좌, waterfall" },
  { category: "Trust Risk", item: "신탁·담보·Covenant", owner: "법무/금융", evidence: "신탁계약, covenant list" },
  { category: "Legal Risk", item: "EOD 및 법률문서 완결성", owner: "법무", evidence: "대출약정서, EOD 조항" },
  { category: "Exit Risk", item: "Exit·미분양·상환 시나리오", owner: "심사", evidence: "Exit sale rate, 미분양 LTV matrix" },
];

export default async function RiskReviewPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  let risk: RiskMatrixResponse = {};
  try {
    const res = await fetch(`${apiBase}/projects/${resolvedParams.id}/risk/assess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      cache: "no-store",
    });
    risk = (await res.json()) as RiskMatrixResponse;
  } catch {
    risk = {};
  }

  const assessedItems = risk.items ?? [];
  const categories = risk.categories ?? [];
  const overall = risk.overall ?? {};
  const controls = riskControls.map((control, index) => {
    const assessed = assessedItems.find((item) => item.category === control.category || item.item === control.item);
    return { ...control, index: index + 1, assessed };
  });

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start gap-3">
        <Link
          href={`/project/${resolvedParams.id}`}
          className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
          aria-label="프로젝트로 돌아가기"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <p className="text-sm font-semibold text-slate-500">프로젝트 #{resolvedParams.id}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <ShieldCheck className="h-7 w-7 text-indigo-600" />
            리스크 관리
          </h2>
          <p className="mt-1 text-sm text-slate-500">PF 심사 기준 20개 관리항목을 각각 확인하고 보완증빙을 추적합니다.</p>
        </div>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label="종합 등급" value={overall.overall_grade ?? "미평가"} tone={gradeTone(overall.overall_grade)} icon={<ShieldCheck />} />
        <SummaryCard label="평균 점수" value={overall.average_score !== undefined ? overall.average_score.toFixed(1) : "미평가"} tone="neutral" icon={<CircleDot />} />
        <SummaryCard label="Critical 항목" value={`${overall.critical_count ?? 0}건`} tone={(overall.critical_count ?? 0) > 0 ? "bad" : "good"} icon={<AlertTriangle />} />
        <SummaryCard label="관리항목" value="20개" tone="neutral" icon={<BadgeCheck />} />
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <FileWarning className="h-5 w-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">20개 리스크 관리항목</h3>
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          {controls.map((control) => (
            <RiskControlCard key={`${control.index}-${control.item}`} control={control} />
          ))}
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center gap-2">
            <Building2 className="h-5 w-5 text-indigo-600" />
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">카테고리별 평가</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-2">Category</th>
                  <th className="px-3 py-2">항목 수</th>
                  <th className="px-3 py-2">평균</th>
                  <th className="px-3 py-2">최대</th>
                  <th className="px-3 py-2">등급</th>
                  <th className="px-3 py-2">Top item</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {categories.map((row) => (
                  <tr key={row.category}>
                    <td className="px-3 py-3 font-semibold text-slate-950 dark:text-white">{translateCategory(row.category)}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.item_count ?? 0}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.average_score?.toFixed(1) ?? "0.0"}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.max_score ?? 0}</td>
                    <td className="px-3 py-3"><GradePill grade={row.grade ?? "Not Assessed"} /></td>
                    <td className="px-3 py-3 text-slate-500">{row.top_item || "미평가"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-6">
          <SidePanel title="Top Risks" icon={<TrendingDown className="h-5 w-5" />}>
            {(risk.topRisks ?? []).length ? (
              <div className="space-y-3">
                {(risk.topRisks ?? []).map((item, index) => (
                  <div key={`${item.category}-${item.item}-${index}`} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                    <p className="text-sm font-bold text-slate-950 dark:text-white">{item.item}</p>
                    <p className="mt-1 text-xs text-slate-500">{translateCategory(item.category)} · score {item.score ?? 0}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">아직 평가된 top risk가 없습니다.</p>
            )}
          </SidePanel>

          <SidePanel title="권고사항" icon={<CheckCircle2 className="h-5 w-5" />}>
            <div className="space-y-2">
              {(risk.recommendations ?? ["표준 모니터링과 covenant 점검을 유지합니다."]).map((rec, index) => (
                <p key={`${rec}-${index}`} className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-800/70 dark:text-slate-200">
                  {rec}
                </p>
              ))}
            </div>
          </SidePanel>
        </div>
      </section>
    </div>
  );
}

function RiskControlCard({
  control,
}: {
  control: {
    index: number;
    category: string;
    item: string;
    owner: string;
    evidence: string;
    assessed?: RiskItem;
  };
}) {
  const grade = control.assessed?.grade ?? "Not Assessed";
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-bold text-slate-400">{String(control.index).padStart(2, "0")} · {translateCategory(control.category)}</p>
          <h4 className="mt-1 text-base font-black text-slate-950 dark:text-white">{control.item}</h4>
        </div>
        <GradePill grade={grade} />
      </div>
      <div className="mt-3 grid gap-2 text-xs text-slate-500 md:grid-cols-2">
        <p><span className="font-bold text-slate-700 dark:text-slate-300">담당</span> {control.owner}</p>
        <p><span className="font-bold text-slate-700 dark:text-slate-300">Score</span> {control.assessed?.score ?? "미평가"}</p>
      </div>
      <p className="mt-2 text-xs text-slate-500"><span className="font-bold text-slate-700 dark:text-slate-300">필요 증빙</span> {control.evidence}</p>
      {control.assessed?.mitigation && (
        <p className="mt-3 rounded-md bg-slate-50 p-2 text-xs text-slate-700 dark:bg-slate-800/70 dark:text-slate-200">{control.assessed.mitigation}</p>
      )}
    </div>
  );
}

function SummaryCard({ label, value, tone, icon }: { label: string; value: string; tone: "good" | "warn" | "bad" | "neutral"; icon: ReactNode }) {
  const toneClass = {
    good: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100",
    warn: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100",
    bad: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100",
    neutral: "border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
  }[tone];
  return (
    <div className={`rounded-lg border p-4 ${toneClass}`}>
      <div className="flex items-center gap-2">
        <div className="h-5 w-5">{icon}</div>
        <p className="text-xs font-semibold">{label}</p>
      </div>
      <p className="mt-3 text-xl font-black">{value}</p>
    </div>
  );
}

function SidePanel({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2 text-slate-950 dark:text-white">
        {icon}
        <h3 className="text-lg font-bold">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function GradePill({ grade }: { grade: string }) {
  const tone = gradeTone(grade);
  const toneClass = {
    good: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200",
    warn: "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-200",
    bad: "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200",
    neutral: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  }[tone];
  return <span className={`inline-flex shrink-0 rounded px-2 py-1 text-xs font-bold ${toneClass}`}>{translateGrade(grade)}</span>;
}

function gradeTone(grade?: string): "good" | "warn" | "bad" | "neutral" {
  if (!grade || grade === "Not Assessed" || grade === "Unknown") return "neutral";
  if (grade === "Low") return "good";
  if (grade === "Moderate") return "warn";
  return "bad";
}

function translateCategory(category?: string) {
  const map: Record<string, string> = {
    "Land Risk": "토지",
    "Permit Risk": "인허가",
    "Developer Risk": "시행사",
    "Contractor Risk": "시공사",
    "Trust Risk": "신탁/담보",
    "Market Risk": "시장",
    "Sales Risk": "분양",
    "Construction Cost Risk": "공사비",
    "Financing Risk": "금융",
    "Exit Risk": "Exit",
    "Legal Risk": "법률",
    "Public Sector Risk": "공공",
  };
  return category ? map[category] ?? category : "미분류";
}

function translateGrade(grade?: string) {
  const map: Record<string, string> = {
    Low: "낮음",
    Moderate: "보통",
    High: "높음",
    Critical: "중대",
    "Not Assessed": "미평가",
    Unknown: "미확인",
  };
  return grade ? map[grade] ?? grade : "미평가";
}
