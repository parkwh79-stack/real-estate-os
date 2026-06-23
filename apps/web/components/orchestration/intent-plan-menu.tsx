"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Clipboard,
  FileSpreadsheet,
  GitBranch,
  ListChecks,
  Loader2,
  Sparkles,
} from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

interface WorkflowOption {
  key: string;
  nameKo: string;
  description: string;
  availableNow: boolean;
  recommended: boolean;
  missingData: string[];
}

interface OutputOption {
  key: string;
  nameKo: string;
  description: string;
  defaultSelected?: boolean;
}

interface ValidationOption {
  key: string;
  nameKo: string;
  description: string;
  defaultSelected?: boolean;
}

interface PromptDraft {
  title: string;
  body: string;
  intendedTool: string;
}

interface PlanResult {
  workflows: WorkflowOption[];
  outputOptions?: OutputOption[];
  validationOptions?: ValidationOption[];
  promptDraft?: PromptDraft | null;
  promptArtifact?: {
    artifactId?: string;
    workflow?: string | null;
    createdAt?: string;
  };
  recommendedWorkflow: string | null;
  promptKo: string;
  summary?: string;
}

export function IntentPlanMenu({
  projectId,
  onWorkflowComplete,
}: {
  projectId: string;
  onWorkflowComplete?: (workflow: string, run: Record<string, unknown>) => void;
}) {
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function postJson(endpoint: string, payload: unknown) {
    const res = await fetch(`${apiBase}/projects/${projectId}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders("web") },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(typeof body.detail === "string" ? body.detail : `요청 실패 (${res.status})`);
    }
    return (await res.json()) as Record<string, unknown>;
  }

  async function loadPlan() {
    setBusy("plan");
    setError(null);
    setNotice(null);
    setCopied(false);
    try {
      const body = await postJson("orchestration/plan", {});
      setPlan(body as unknown as PlanResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "분석 메뉴를 불러오지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function runWorkflow(workflow: string) {
    setBusy(`run:${workflow}`);
    setError(null);
    setNotice(null);
    try {
      const run = await postJson("orchestration/run-workflow", { workflow });
      const status = (run.status as string) ?? "completed";
      const steps = Array.isArray(run.steps) ? (run.steps as unknown[]).length : null;
      setNotice(
        steps != null
          ? `'${workflow}' 실행 완료 (상태: ${status}, ${steps}개 단계)`
          : `'${workflow}' 실행 완료 (상태: ${status})`,
      );
      onWorkflowComplete?.(workflow, run);
    } catch (err) {
      setError(err instanceof Error ? err.message : "선택한 분석 실행에 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function copyPrompt() {
    if (!plan?.promptDraft?.body) return;
    await navigator.clipboard.writeText(plan.promptDraft.body);
    await postJson("prompt-artifacts", {
      artifactType: "copied_prompt",
      workflow: plan.recommendedWorkflow,
      title: plan.promptDraft.title,
      body: plan.promptDraft.body,
      intendedTool: plan.promptDraft.intendedTool,
      source: "web_copy_button",
      parentArtifactId: plan.promptArtifact?.artifactId,
      outputOptions: plan.outputOptions ?? [],
      validationOptions: plan.validationOptions ?? [],
    }).catch(() => null);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">AI 업무 주문서</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              업로드된 자료를 기준으로 가능한 업무, 필요한 산출물, 검증조건, Excel/AI 작업지시서를 자동으로 정리합니다.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void loadPlan()}
          disabled={busy !== null}
          className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white disabled:opacity-40 dark:bg-white dark:text-slate-950"
        >
          {busy === "plan" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListChecks className="h-4 w-4" />}
          {plan ? "다시 진단" : "업무 진단하기"}
        </button>
      </div>

      {notice ? <Message tone="good" text={notice} /> : null}
      {error ? <Message tone="bad" text={error} /> : null}

      {plan ? (
        <div className="mt-5 space-y-5">
          <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-950/40">
            <p className="text-sm font-bold text-slate-950 dark:text-white">AI 판단 요약</p>
            <p className="mt-2 text-sm font-semibold leading-6 text-slate-600 dark:text-slate-300">{plan.promptKo}</p>
            {plan.summary ? <p className="mt-1 text-xs text-slate-500">{plan.summary}</p> : null}
            {plan.promptArtifact?.artifactId ? (
              <p className="mt-2 text-xs font-semibold text-slate-500">
                저장된 업무 주문서 ID: {plan.promptArtifact.artifactId}
              </p>
            ) : null}
          </div>

          <div>
            <p className="mb-2 text-sm font-bold text-slate-950 dark:text-white">1. 지금 실행할 업무</p>
            <div className="grid gap-2">
              {plan.workflows.map((workflow) => {
                const running = busy === `run:${workflow.key}`;
                return (
                  <button
                    key={workflow.key}
                    type="button"
                    onClick={() => void runWorkflow(workflow.key)}
                    disabled={!workflow.availableNow || busy !== null}
                    className="flex w-full items-start gap-3 rounded-lg border border-slate-200 bg-white p-3 text-left transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-800 dark:bg-slate-950/40"
                  >
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                      {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-black text-slate-950 dark:text-white">{workflow.nameKo}</p>
                        {workflow.recommended ? <Badge label="추천" tone="amber" /> : null}
                        {!workflow.availableNow ? <Badge label="자료 부족" tone="rose" /> : null}
                      </div>
                      <p className="mt-0.5 text-xs font-semibold leading-5 text-slate-500">{workflow.description}</p>
                      {!workflow.availableNow && workflow.missingData.length > 0 ? (
                        <p className="mt-1 text-xs font-bold text-rose-600 dark:text-rose-300">
                          부족 자료: {workflow.missingData.join(", ")}
                        </p>
                      ) : null}
                    </div>
                    {workflow.availableNow ? <ArrowRight className="mt-2 h-4 w-4 shrink-0 text-slate-400" /> : null}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <OptionPanel title="2. 만들 산출물" icon={<FileSpreadsheet className="h-4 w-4" />}>
              {(plan.outputOptions ?? []).map((option) => (
                <OptionRow key={option.key} title={option.nameKo} body={option.description} checked={Boolean(option.defaultSelected)} />
              ))}
            </OptionPanel>

            <OptionPanel title="3. 붙일 검증조건" icon={<CheckCircle2 className="h-4 w-4" />}>
              {(plan.validationOptions ?? []).map((option) => (
                <OptionRow key={option.key} title={option.nameKo} body={option.description} checked={Boolean(option.defaultSelected)} />
              ))}
            </OptionPanel>
          </div>

          {plan.promptDraft ? (
            <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-4 dark:border-indigo-950 dark:bg-indigo-950/20">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-black text-indigo-950 dark:text-indigo-100">{plan.promptDraft.title}</p>
                  <p className="mt-1 text-xs font-semibold text-indigo-700 dark:text-indigo-300">{plan.promptDraft.intendedTool}</p>
                </div>
                <button
                  type="button"
                  onClick={() => void copyPrompt()}
                  className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 text-xs font-bold text-white hover:bg-indigo-700"
                >
                  <Clipboard className="h-4 w-4" />
                  {copied ? "복사됨" : "작업지시서 복사"}
                </button>
              </div>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-xs leading-6 text-slate-700 dark:bg-slate-950 dark:text-slate-200">
                {plan.promptDraft.body}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Message({ tone, text }: { tone: "good" | "bad"; text: string }) {
  const klass =
    tone === "good"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200"
      : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-200";
  return <div className={`mt-3 rounded-lg border p-3 text-sm font-bold ${klass}`}>{text}</div>;
}

function Badge({ label, tone }: { label: string; tone: "amber" | "rose" }) {
  const klass =
    tone === "amber"
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
      : "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200";
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-black ${klass}`}>{label}</span>;
}

function OptionPanel({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-center gap-2 text-sm font-bold text-slate-950 dark:text-white">
        {icon}
        {title}
      </div>
      <div className="mt-3 space-y-2">{children}</div>
    </div>
  );
}

function OptionRow({ title, body, checked }: { title: string; body: string; checked: boolean }) {
  return (
    <div className="flex items-start gap-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-950/40">
      <span
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
          checked
            ? "border-emerald-500 bg-emerald-500 text-white"
            : "border-slate-300 bg-white text-transparent dark:border-slate-700 dark:bg-slate-900"
        }`}
      >
        <CheckCircle2 className="h-3.5 w-3.5" />
      </span>
      <div>
        <p className="text-sm font-bold text-slate-950 dark:text-white">{title}</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">{body}</p>
      </div>
    </div>
  );
}
