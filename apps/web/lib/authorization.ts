export const PROJECT_WRITE_PERMISSION = "project:write";

export function hasPermission(
  permissions: readonly string[],
  permission: string,
): boolean {
  return permissions.includes(permission);
}

export function projectWriteRestrictionMessage(
  role?: string,
  action = "새 심사 생성과 자료 업로드",
): string {
  const subject = role ? `현재 역할 '${role}'` : "현재 계정";
  return `${subject}에는 '${PROJECT_WRITE_PERMISSION}' 권한이 없습니다. ${action}은 Tenant 관리자에게 analyst 또는 admin 역할 승인을 받은 뒤 사용할 수 있습니다.`;
}
