"use client";

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ExternalLink,
  FileBadge,
  FileSearch,
  Loader2,
  Scale,
  Search,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getLegalResource,
  searchLegalResources,
} from "../../lib/legal-intelligence-client";
import type {
  LegalResourceDetail,
  LegalResourceSummary,
  LegalResourceType,
  LegalSearchResponse,
  LegalVerificationStatus,
} from "../../lib/legal-intelligence-types";

const RESOURCE_TYPES: Array<{ value: LegalResourceType | "all"; label: string }> = [
  { value: "all", label: "전체 법률자료" },
  { value: "statute", label: "법령" },
  { value: "provision", label: "조문" },
  { value: "precedent", label: "판례" },
  { value: "interpretation", label: "법령해석" },
  { value: "administrative_rule", label: "행정규칙" },
  { value: "local_ordinance", label: "자치법규" },
  { value: "constitutional_decision", label: "헌재결정" },
  { value: "committee_decision", label: "위원회 결정" },
  { value: "legal_term", label: "법령용어" },
];

const RESOURCE_LABEL: Record<LegalResourceType, string> = Object.fromEntries(
  RESOURCE_TYPES.filter(
    (item): item is { value: LegalResourceType; label: string } =>
      item.value !== "all",
  ).map((item) => [item.value, item.label]),
) as Record<LegalResourceType, string>;

const AUTHORITY_LABEL: Record<string, string> = {
  law_go_kr: "국가법령정보센터",
  supreme_court: "대한민국 법원",
  constitutional_court: "헌법재판소",
  central_government: "중앙행정기관",
  local_government: "지방자치단체",
  public_agency: "공공기관",
  national_tax_service: "국세청",
  tenant_document: "프로젝트 문서",
  human_counsel: "법무 담당자",
  caller_supplied: "사용자 제공 후보",
  unknown: "출처 미확인",
};

const TRANSPORT_LABEL: Record<string, string> = {
  local_verified_snapshot: "검증된 로컬 스냅샷",
  law_go_kr_direct_api: "공식 API 직접 조회",
  self_hosted_mcp: "내부 MCP 도구",
  remote_mcp: "외부 MCP 도구",
  tenant_object_storage: "Tenant 저장소",
  caller_context: "사용자 입력",
  local_database: "로컬 데이터베이스",
  direct_api: "공식 API 직접 조회",
  manual: "수기 등록",
  unknown: "조회 경로 미확인",
};

export function LegalIntelligencePanel() {
  const [query, setQuery] = useState("");
  const [resourceType, setResourceType] = useState<LegalResourceType | "all">(
    "all",
  );
  const [asOf, setAsOf] = useState(todayLocalDate);
  const [publicQueryAcknowledged, setPublicQueryAcknowledged] = useState(false);
  const [searchResult, setSearchResult] = useState<LegalSearchResponse | null>(
    null,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LegalResourceDetail | null>(null);
  const [searching, setSearching] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      searchAbortRef.current?.abort();
      detailAbortRef.current?.abort();
    },
    [],
  );

  const loadDetail = useCallback(async (item: LegalResourceSummary) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    setSelectedId(item.resourceId);
    setDetail(item);
    setDetailError(null);
    setLoadingDetail(true);
    try {
      const nextDetail = await getLegalResource(item.resourceId, controller.signal);
      if (!controller.signal.aborted) setDetail(nextDetail);
    } catch (error) {
      if (controller.signal.aborted) return;
      setDetailError(
        error instanceof Error
          ? error.message
          : "법률정보 상세를 불러오지 못했습니다.",
      );
    } finally {
      if (!controller.signal.aborted) setLoadingDetail(false);
    }
  }, []);

  const runSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchError("법령명, 조문, 사건번호 또는 검색어를 입력해 주세요.");
      return;
    }
    searchAbortRef.current?.abort();
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    setSearching(true);
    setSearchError(null);
    setDetailError(null);
    setSelectedId(null);
    setDetail(null);
    try {
      const result = await searchLegalResources(
        {
          query: trimmed,
          resourceType,
          asOf,
          publicQueryAcknowledged,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setSearchResult(result);
      const first = result.results[0];
      if (first) void loadDetail(first);
    } catch (error) {
      if (controller.signal.aborted) return;
      setSearchResult(null);
      setSearchError(
        error instanceof Error ? error.message : "법률정보 검색에 실패했습니다.",
      );
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  }, [asOf, loadDetail, publicQueryAcknowledged, query, resourceType]);

  const verifiedCount = useMemo(
    () => searchResult?.results.filter((item) => isVerified(item.verificationStatus)).length ?? 0,
    [searchResult],
  );

  return (
    <section className="overflow-hidden rounded-lg border border-cyan-200 bg-white shadow-sm dark:border-cyan-900 dark:bg-slate-900">
      <div className="border-b border-cyan-100 bg-cyan-50/70 p-4 dark:border-cyan-950 dark:bg-cyan-950/20">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <p className="text-xs font-black uppercase tracking-[0.16em] text-cyan-700 dark:text-cyan-300">
              Legal Intelligence
            </p>
            <h2 className="mt-1 text-lg font-black text-slate-950 dark:text-white">
              검증된 법률자료 검색
            </h2>
            <p className="mt-1 max-w-3xl text-xs font-semibold leading-5 text-slate-600 dark:text-slate-300">
              공식 출처와 기준일을 확인하는 조사 화면입니다. 검색 결과나 AI 요약은
              법률의견 또는 프로젝트 승인으로 간주되지 않습니다.
            </p>
          </div>
          <span className="inline-flex w-fit items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-black text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            <AlertTriangle className="h-3.5 w-3.5" />
            예비 법률조사 · 사람 검토 필요
          </span>
        </div>

        <form
          className="mt-4 grid gap-2 lg:grid-cols-[minmax(16rem,1fr)_11rem_10rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            void runSearch();
          }}
        >
          <label className="relative block">
            <span className="sr-only">법률정보 검색어</span>
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="예: 건축법 제11조, 2024다12345"
              className="h-10 w-full rounded-md border border-cyan-200 bg-white pl-9 pr-3 text-sm font-semibold text-slate-900 outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 dark:border-cyan-800 dark:bg-slate-950 dark:text-white"
            />
          </label>
          <label>
            <span className="sr-only">법률자료 유형</span>
            <select
              value={resourceType}
              onChange={(event) =>
                setResourceType(event.target.value as LegalResourceType | "all")
              }
              className="h-10 w-full rounded-md border border-cyan-200 bg-white px-3 text-sm font-bold text-slate-800 outline-none focus:border-cyan-500 dark:border-cyan-800 dark:bg-slate-950 dark:text-white"
            >
              {RESOURCE_TYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">법령 기준일</span>
            <input
              type="date"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              max="9999-12-31"
              className="h-10 w-full rounded-md border border-cyan-200 bg-white px-3 text-sm font-bold text-slate-800 outline-none focus:border-cyan-500 dark:border-cyan-800 dark:bg-slate-950 dark:text-white"
            />
          </label>
          <button
            type="submit"
            disabled={searching}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-cyan-700 px-4 text-sm font-black text-white hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {searching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileSearch className="h-4 w-4" />
            )}
            {searching ? "검색 중" : "법률자료 검색"}
          </button>
        </form>
        <label className="mt-3 flex max-w-4xl items-start gap-2 rounded-md border border-cyan-100 bg-white/80 px-3 py-2 text-xs font-semibold leading-5 text-slate-700 dark:border-cyan-900 dark:bg-slate-950/60 dark:text-slate-200">
          <input
            type="checkbox"
            checked={publicQueryAcknowledged}
            onChange={(event) =>
              setPublicQueryAcknowledged(event.target.checked)
            }
            className="mt-0.5 h-4 w-4 rounded border-cyan-300 text-cyan-700 focus:ring-cyan-500"
          />
          <span>
            승인된 외부 법률 공급자 조회를 허용합니다. 검색어에 프로젝트
            주소·PNU·계약 내용·개인정보 등 비공개 정보가 없음을 확인한 경우에만
            선택하세요. 선택하지 않으면 검증된 로컬 DB와 신뢰된 내부 도구만
            조회합니다.
          </span>
        </label>
        {searchError ? (
          <div role="alert" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
            {searchError}
          </div>
        ) : null}
      </div>

      {searching ? (
        <LegalSearchSkeleton />
      ) : searchResult && searchResult.results.length === 0 ? (
        <div className="p-6">
          <EmptyLegalState
            title="일치하는 법률자료가 없습니다"
            body="검색어·자료 유형·기준일을 조정해 보세요. 공식 자료가 동기화되지 않은 경우 운영 담당자의 소스 갱신이 필요할 수 있습니다."
          />
        </div>
      ) : searchResult ? (
        <>
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2 text-[11px] font-bold text-slate-600 dark:border-slate-800 dark:text-slate-300">
            <span>검색 결과 {searchResult.results.length}건</span>
            <span>·</span>
            <span className="text-emerald-700 dark:text-emerald-300">
              검증 상태 {verifiedCount}건
            </span>
            <span>·</span>
            <span>기준일 {formatDate(searchResult.asOfDate ?? asOf)}</span>
            {searchResult.satisfiedByLocalSnapshot ? (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                검증된 로컬 스냅샷
              </span>
            ) : null}
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {searchResult.externalEgressAcknowledged
                ? "공개 질의 · 외부 조회 허용"
                : "로컬·신뢰된 내부 조회만"}
            </span>
          </div>
          <div className="grid min-h-[28rem] xl:grid-cols-[0.82fr_1.18fr]">
            <div className="max-h-[42rem] overflow-y-auto border-b border-slate-200 p-3 xl:border-b-0 xl:border-r dark:border-slate-800">
              <div className="space-y-2">
                {searchResult.results.map((item) => (
                  <LegalResultButton
                    key={`${item.resourceId}-${item.resourceVersionId ?? ""}`}
                    item={item}
                    selected={
                      selectedId === item.resourceId &&
                      detail?.resourceVersionId === item.resourceVersionId
                    }
                    onSelect={() => void loadDetail(item)}
                  />
                ))}
              </div>
            </div>
            <div className="min-w-0 p-4">
              {loadingDetail && !detail ? (
                <LegalDetailSkeleton />
              ) : detail ? (
                <LegalResourceDetailView
                  item={detail}
                  loading={loadingDetail}
                  error={detailError}
                />
              ) : (
                <EmptyLegalState
                  title="상세 확인할 자료를 선택해 주세요"
                  body="원문, 출처, 검증 상태와 시행기간을 한 화면에서 확인할 수 있습니다."
                />
              )}
            </div>
          </div>
        </>
      ) : (
        <div className="p-6">
          <EmptyLegalState
            title="법률자료를 검색해 주세요"
            body="법령명·조문·사건번호는 정확 검색이 우선되며, 기준일에 유효한 자료만 확인할 수 있습니다."
          />
        </div>
      )}
    </section>
  );
}

function LegalResultButton({
  item,
  selected,
  onSelect,
}: {
  item: LegalResourceSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`w-full rounded-md border p-3 text-left transition ${
        selected
          ? "border-cyan-400 bg-cyan-50 ring-1 ring-cyan-200 dark:border-cyan-700 dark:bg-cyan-950/30"
          : "border-slate-200 bg-white hover:border-cyan-200 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-950 dark:hover:border-cyan-900"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="slate">{RESOURCE_LABEL[item.resourceType]}</Badge>
        <VerificationBadge status={item.verificationStatus} />
        {item.stale ? <Badge tone="amber">기준일 확인 필요</Badge> : null}
      </div>
      <p className="mt-2 text-sm font-black text-slate-950 dark:text-white">
        {item.title}
      </p>
      {item.citation ? (
        <p className="mt-1 text-xs font-bold text-cyan-800 dark:text-cyan-200">
          {item.citation}
        </p>
      ) : null}
      {item.excerpt ? (
        <p className="mt-1 line-clamp-3 text-xs leading-5 text-slate-600 dark:text-slate-300">
          {item.excerpt}
        </p>
      ) : null}
      <p className="mt-2 text-[11px] font-semibold text-slate-500">
        {AUTHORITY_LABEL[item.sourceAuthority] ?? item.sourceAuthority} ·{" "}
        {formatDate(item.effectiveFrom)}~{formatDate(item.effectiveTo)}
      </p>
    </button>
  );
}

function LegalResourceDetailView({
  item,
  loading,
  error,
}: {
  item: LegalResourceDetail;
  loading: boolean;
  error: string | null;
}) {
  return (
    <div className="relative">
      {loading ? (
        <div className="absolute right-0 top-0 inline-flex items-center gap-1 text-[11px] font-bold text-cyan-700">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          최신 상세 확인 중
        </div>
      ) : null}
      <div className="pr-28">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="slate">{RESOURCE_LABEL[item.resourceType]}</Badge>
          <VerificationBadge status={item.verificationStatus} />
          <Badge tone={item.stale ? "amber" : "emerald"}>
            {item.stale
              ? "Stale · 승인근거 사용 금지"
              : item.freshnessStatus === "unknown"
                ? "신선도 미확인"
                : "현재성 확인"}
          </Badge>
        </div>
        <h3 className="mt-2 text-xl font-black text-slate-950 dark:text-white">
          {item.title}
        </h3>
        {item.citation ? (
          <p className="mt-1 text-sm font-bold text-cyan-800 dark:text-cyan-200">
            {item.citation}
          </p>
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {error} 검색 목록에 포함된 요약 정보만 표시합니다.
        </div>
      ) : null}

      <dl className="mt-4 grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-950">
        <Meta label="출처기관" value={AUTHORITY_LABEL[item.sourceAuthority] ?? item.sourceAuthority} />
        <Meta label="조회경로" value={TRANSPORT_LABEL[item.transport] ?? item.transport} />
        <Meta label="검색 기준일" value={formatDate(item.asOfDate)} />
        <Meta
          label="법적 유효기간"
          value={`${formatDate(item.effectiveFrom)} ~ ${formatDate(item.effectiveTo)}`}
        />
        <Meta label="검증시각" value={formatTimestamp(item.verifiedAt)} />
        <Meta label="공식 Resource ID" value={item.resourceId || "미확인"} />
      </dl>

      {item.sourceUrl && isSafeWebUrl(item.sourceUrl) ? (
        <a
          href={item.sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1 rounded-md border border-cyan-200 px-3 py-2 text-xs font-black text-cyan-800 hover:bg-cyan-50 dark:border-cyan-800 dark:text-cyan-200 dark:hover:bg-cyan-950/30"
        >
          정확한 공식 원문 위치 열기
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : (
        <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          정확한 공식 원문 URL이 연결되지 않았습니다. 이 자료를 승인 근거로
          사용하기 전에 Evidence Locator를 보강해야 합니다.
        </p>
      )}

      <div className="mt-4 space-y-3">
        <ContentSection
          badge="공식 원문"
          title="출처기관 원문"
          icon={<FileBadge className="h-4 w-4" />}
          tone="cyan"
          text={item.officialText}
          empty="상세 응답에 공식 원문이 포함되지 않았습니다. 위 공식 링크에서 원문을 확인하세요."
        />
        <ContentSection
          badge="AI 요약"
          title="기계 생성 참고 요약"
          icon={<Bot className="h-4 w-4" />}
          tone="violet"
          text={item.aiSummary}
          empty="저장된 AI 요약이 없습니다. 원문이 AI 설명으로 대체되지 않습니다."
        />
        <ContentSection
          badge="사람 의견"
          title="법무 담당자 검토 의견"
          icon={<UserRoundCheck className="h-4 w-4" />}
          tone="emerald"
          text={item.humanOpinion}
          empty="승인된 법무 담당자 의견이 아직 없습니다."
        />
      </div>

      <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <strong>사용 제한:</strong> 공식 인용의 확인 여부와 최종 법률판단은
        다릅니다. 이 검색 결과는 예비 조사 자료이며, 프로젝트 승인·계약 해석·소송
        판단에는 담당 법무 또는 변호사의 별도 검토가 필요합니다.
      </div>
    </div>
  );
}

function ContentSection({
  badge,
  title,
  icon,
  tone,
  text,
  empty,
}: {
  badge: string;
  title: string;
  icon: React.ReactNode;
  tone: "cyan" | "violet" | "emerald";
  text?: string;
  empty: string;
}) {
  const classes = {
    cyan: "border-cyan-200 bg-cyan-50 text-cyan-900 dark:border-cyan-900 dark:bg-cyan-950/20 dark:text-cyan-100",
    violet:
      "border-violet-200 bg-violet-50 text-violet-900 dark:border-violet-900 dark:bg-violet-950/20 dark:text-violet-100",
    emerald:
      "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-100",
  }[tone];
  return (
    <section className={`rounded-md border p-3 ${classes}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-white/80 px-2 py-1 text-[10px] font-black dark:bg-slate-950/70">
          {icon}
          {badge}
        </span>
        <h4 className="text-xs font-black">{title}</h4>
      </div>
      <p
        className={`mt-2 whitespace-pre-wrap text-xs leading-5 ${
          text ? "" : "opacity-65"
        }`}
      >
        {text ?? empty}
      </p>
    </section>
  );
}

function VerificationBadge({ status }: { status: LegalVerificationStatus }) {
  const verified = isVerified(status);
  const failed = status === "failed";
  return (
    <Badge tone={verified ? "emerald" : failed ? "rose" : "amber"}>
      {verified ? (
        <CheckCircle2 className="h-3 w-3" />
      ) : failed ? (
        <AlertTriangle className="h-3 w-3" />
      ) : (
        <ShieldCheck className="h-3 w-3" />
      )}
      {verificationLabel(status)}
    </Badge>
  );
}

function Badge({
  tone,
  children,
}: {
  tone: "slate" | "cyan" | "emerald" | "amber" | "rose";
  children: React.ReactNode;
}) {
  const classes = {
    slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
    cyan: "bg-cyan-100 text-cyan-800 dark:bg-cyan-950 dark:text-cyan-200",
    emerald:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
    amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    rose: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  }[tone];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-black ${classes}`}
    >
      {children}
    </span>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-bold text-slate-500">{label}</dt>
      <dd className="mt-0.5 break-all font-bold text-slate-800 dark:text-slate-100">
        {value}
      </dd>
    </div>
  );
}

function EmptyLegalState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-md border border-dashed border-slate-200 px-5 py-8 text-center dark:border-slate-800">
      <Scale className="h-8 w-8 text-slate-300 dark:text-slate-700" />
      <p className="mt-3 text-sm font-black text-slate-700 dark:text-slate-200">
        {title}
      </p>
      <p className="mt-1 max-w-lg text-xs leading-5 text-slate-500">{body}</p>
    </div>
  );
}

function LegalSearchSkeleton() {
  return (
    <div className="grid min-h-[28rem] animate-pulse gap-4 p-4 xl:grid-cols-[0.82fr_1.18fr]">
      <div className="space-y-3">
        {[1, 2, 3].map((item) => (
          <div key={item} className="h-28 rounded-md bg-slate-100 dark:bg-slate-800" />
        ))}
      </div>
      <LegalDetailSkeleton />
    </div>
  );
}

function LegalDetailSkeleton() {
  return (
    <div className="animate-pulse space-y-3">
      <div className="h-5 w-32 rounded bg-slate-100 dark:bg-slate-800" />
      <div className="h-8 w-2/3 rounded bg-slate-100 dark:bg-slate-800" />
      <div className="h-24 rounded bg-slate-100 dark:bg-slate-800" />
      <div className="h-32 rounded bg-slate-100 dark:bg-slate-800" />
    </div>
  );
}

function isVerified(status: LegalVerificationStatus): boolean {
  return [
    "human_reviewed",
    "verified_snapshot",
    "source_attested",
    "citation_verified",
    "verified",
  ].includes(status);
}

function verificationLabel(status: LegalVerificationStatus): string {
  return {
    human_reviewed: "사람 검토 완료",
    verified_snapshot: "검증 스냅샷",
    source_attested: "공식 출처 확인",
    citation_verified: "인용 검증",
    verified: "검증됨",
    candidate: "검증 후보",
    untrusted_candidate: "비신뢰 후보",
    failed: "검증 실패",
    stale: "기준일 확인 필요",
    not_verified: "미검증",
    unknown: "검증상태 미확인",
  }[status];
}

function todayLocalDate(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function formatDate(value?: string): string {
  if (!value) return "미확인";
  const dateOnly = value.slice(0, 10);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateOnly);
  return match ? `${match[1]}.${match[2]}.${match[3]}` : value;
}

function formatTimestamp(value?: string): string {
  if (!value) return "미확인";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function isSafeWebUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}
