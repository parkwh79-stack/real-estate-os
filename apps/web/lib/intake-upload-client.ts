import { apiBase, authHeaders } from "./auth-client";
import { backendErrorMessageForResponse } from "./backend-error";

// Leave multipart/header headroom below Vercel's 4.5 MB function limit.
export const INTAKE_PART_BYTES = 3 * 1024 * 1024;
export const INTAKE_MAX_FILE_BYTES = 64 * 1024 * 1024;

export type IntakeClassifiedFile = {
  filename: string;
  documentType: string;
  label: string;
  confidence: number;
  confidenceBasis?: string;
  needsReview: boolean;
  archiveStored?: boolean;
  reasons?: string[];
  transferId?: string;
};

type Transfer = { transferId: string; partSizeBytes: number; partCount: number; nextPart: number; expiresAt: string };
// Retain completed parts for an explicit retry on the same page. Never reuse a
// transfer across projects, and never persist a file/capability in browser storage.
const transfers = new WeakMap<File, Map<string, Transfer>>();

export function validateIntakeFiles(files: File[]): void {
  for (const file of files) {
    if (!file.size) throw new Error(`${file.name}: 빈 파일입니다. 내용이 있는 파일을 선택해 주세요.`);
    if (file.size > INTAKE_MAX_FILE_BYTES) {
      throw new Error(`${file.name}: 파일 한 개는 64MB까지 올릴 수 있습니다. 문서를 나누어 선택해 주세요.`);
    }
  }
}

async function checkedJson(response: Response, action: string): Promise<Record<string, unknown>> {
  if (!response.ok) throw new Error(await backendErrorMessageForResponse(response, { action }));
  const body: unknown = await response.json();
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error(`${action} 응답을 확인하지 못했습니다. 다시 시도해 주세요.`);
  return body as Record<string, unknown>;
}

async function stageFile(projectId: string, file: File, progress?: (message: string) => void): Promise<Transfer> {
  const cached = transfers.get(file)?.get(projectId);
  const base = `${apiBase}/projects/${encodeURIComponent(projectId)}/intake/transfers`;
  let transfer = cached && Date.parse(cached.expiresAt) > Date.now() ? cached : undefined;
  if (!transfer) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const body = await checkedJson(await fetch(base, {
    method: "POST", headers: { ...authHeaders("web"), "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, contentType: file.type || "application/octet-stream", sizeBytes: file.size, sha256 }),
  }), "파일 전송 준비");
  const { transferId, partSizeBytes, partCount, expiresAt } = body;
  if (typeof transferId !== "string" || !/^[A-Za-z0-9-]{1,80}$/.test(transferId)
    || partSizeBytes !== INTAKE_PART_BYTES || partCount !== Math.ceil(file.size / INTAKE_PART_BYTES)
    || typeof expiresAt !== "string" || !Number.isFinite(Date.parse(expiresAt))) {
    throw new Error("파일 전송 설정을 확인하지 못했습니다. 다시 시도해 주세요.");
  }
  transfer = { transferId, partSizeBytes, partCount, nextPart: 0, expiresAt } as Transfer;
  let byProject = transfers.get(file);
  if (!byProject) { byProject = new Map(); transfers.set(file, byProject); }
  byProject.set(projectId, transfer);
  }
  for (let index = transfer.nextPart; index < transfer.partCount; index += 1) {
    progress?.(`${file.name} · 파일 전송 ${index + 1}/${transfer.partCount}`);
    const response = await fetch(`${base}/${encodeURIComponent(transfer.transferId)}/parts/${index}`, {
      method: "PUT", headers: { ...authHeaders("web"), "Content-Type": "application/octet-stream" },
      body: file.slice(index * INTAKE_PART_BYTES, Math.min(file.size, (index + 1) * INTAKE_PART_BYTES)),
    });
    if ([404, 410].includes(response.status)) transfers.get(file)?.delete(projectId);
    if (!response.ok) throw new Error(await backendErrorMessageForResponse(response, { action: "파일 조각 전송" }));
    transfer.nextPart = index + 1;
  }
  return transfer;
}

function classificationFiles(body: Record<string, unknown>, expected: number): IntakeClassifiedFile[] {
  if (!Array.isArray(body.files) || body.files.length !== expected || body.files.some((file) =>
    !file || typeof file !== "object" || typeof file.filename !== "string" || typeof file.documentType !== "string"
      || typeof file.label !== "string" || typeof file.confidence !== "number" || !Number.isFinite(file.confidence))) {
    throw new Error("선택한 파일과 분류 결과가 일치하지 않습니다. 파일을 유지했으니 다시 시도해 주세요.");
  }
  return body.files as IntakeClassifiedFile[];
}

export async function classifyIntakeFiles(projectId: string, files: File[], progress?: (message: string) => void): Promise<{ files: IntakeClassifiedFile[] }> {
  validateIntakeFiles(files);
  const result: IntakeClassifiedFile[] = [];
  let batch: File[] = [];
  let batchBytes = 0;
  const flush = async () => {
    if (!batch.length) return;
    const form = new FormData();
    batch.forEach((file) => form.append("files", file));
    progress?.(`자료 ${result.length + 1}~${result.length + batch.length}개 분류 중`);
    const body = await checkedJson(await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/intake/classify`, {
      method: "POST", headers: authHeaders("web"), body: form,
    }), "자료 업로드 및 문서 분류");
    result.push(...classificationFiles(body, batch.length));
    batch = []; batchBytes = 0;
  };
  for (const file of files) {
    if (file.size > INTAKE_PART_BYTES) {
      await flush();
      const transfer = await stageFile(projectId, file, progress);
      progress?.(`${file.name} · 문서 분류 중`);
      const url = `${apiBase}/projects/${encodeURIComponent(projectId)}/intake/transfers/${encodeURIComponent(transfer.transferId)}/classify`;
      const response = await fetch(url, { method: "POST", headers: { ...authHeaders("web"), "Content-Type": "application/json" }, body: "{}" });
      if ([404, 410].includes(response.status)) transfers.get(file)?.delete(projectId);
      const body = await checkedJson(response, "자료 업로드 및 문서 분류");
      result.push(...classificationFiles(body, 1).map((classified) => ({ ...classified, transferId: transfer.transferId })));
    } else {
      if (batchBytes + file.size > INTAKE_PART_BYTES || batch.length >= 20) await flush();
      batch.push(file); batchBytes += file.size;
    }
  }
  await flush();
  return { files: result };
}

export async function processTransferredIntakeFile(projectId: string, file: File, documentType: string): Promise<Response> {
  const transfer = await stageFile(projectId, file);
  const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/intake/transfers/${encodeURIComponent(transfer.transferId)}/process`, {
    method: "POST", headers: { ...authHeaders("web"), "Content-Type": "application/json" },
    body: JSON.stringify({ documentType }),
  });
  if ([404, 410].includes(response.status)) transfers.get(file)?.delete(projectId);
  return response;
}
