import { apiBase, authHeaders, type RealyticsUser } from "./auth-client";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export function editableAccountDisplayName(user: RealyticsUser | null): string {
  const name = typeof user?.displayName === "string" ? user.displayName.trim() : "";
  if (!name || UUID.test(name) || name === user?.userId || name === user?.metadata?.subject
    || /::[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(name)) return "";
  return name;
}

export function accountDisplayName(user: RealyticsUser | null): string {
  return editableAccountDisplayName(user) || "내 계정";
}

export async function saveAccountDisplayName(displayName: string): Promise<string> {
  const response = await fetch(`${apiBase}/auth/profile`, {
    method: "PUT", cache: "no-store",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ displayName: displayName.trim() }),
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.");
    if (response.status === 403) throw new Error("이 계정의 이름을 변경할 수 없습니다. 로그인 상태를 확인해 주세요.");
    if (response.status === 422) throw new Error("계정 번호 대신 사용할 이름을 1~200자로 입력해 주세요.");
    throw new Error("이름을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.");
  }
  const body = await response.json().catch(() => null);
  if (body?.schemaVersion !== "realytics-account-profile-v1" || body.displayName !== displayName.trim()) {
    throw new Error("저장 결과를 확인하지 못했습니다. 새로고침해 이름을 확인해 주세요.");
  }
  return body.displayName;
}
