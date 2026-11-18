import { ClipboardCheck, LockKeyhole } from "lucide-react";
import { PROJECT_WRITE_PERMISSION } from "../../lib/authorization";

export function ReviewOnlyNotice({
  role,
  tone = "dark",
}: {
  role?: string;
  tone?: "dark" | "light";
}) {
  const dark = tone === "dark";
  return (
    <aside
      role="status"
      data-testid="review-only-notice"
      className={`flex flex-col gap-3 rounded-2xl border p-4 sm:flex-row sm:items-center sm:justify-between ${
        dark
          ? "border-amber-300/30 bg-amber-400/10 text-amber-50"
          : "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100"
      }`}
    >
      <div className="flex min-w-0 items-start gap-3">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
            dark ? "bg-amber-300/15 text-amber-200" : "bg-amber-100 text-amber-700 dark:bg-amber-900/60 dark:text-amber-200"
          }`}
        >
          <ClipboardCheck className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-black">심사 전용 역할</p>
            {role ? (
              <span
                data-testid="review-only-role"
                className={`rounded-md border px-2 py-0.5 text-[10px] font-black uppercase tracking-wide ${
                  dark
                    ? "border-amber-300/25 bg-slate-950/30 text-amber-100"
                    : "border-amber-300 bg-white/70 text-amber-800 dark:border-amber-700 dark:bg-slate-950/30 dark:text-amber-100"
                }`}
              >
                {role}
              </span>
            ) : null}
          </div>
          <p className={`mt-1 text-xs leading-5 ${dark ? "text-amber-100/75" : "text-amber-900/75 dark:text-amber-100/75"}`}>
            기존 프로젝트와 심사 기록은 열람할 수 있지만 새 심사 생성과 자료 업로드는 사용할 수 없습니다.
          </p>
        </div>
      </div>
      <div
        className={`inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg border px-2.5 py-1.5 text-[11px] font-bold sm:self-auto ${
          dark
            ? "border-amber-300/25 bg-slate-950/30 text-amber-100"
            : "border-amber-300 bg-white/70 text-amber-800 dark:border-amber-700 dark:bg-slate-950/30 dark:text-amber-100"
        }`}
      >
        <LockKeyhole className="h-3.5 w-3.5" aria-hidden="true" />
        필요 권한 · {PROJECT_WRITE_PERMISSION}
      </div>
    </aside>
  );
}
