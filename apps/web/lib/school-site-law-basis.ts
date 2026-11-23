export const SCHOOL_EVENT_LABELS = {
  approval_application: "사업계획 승인 신청",
  approval: "사업계획 승인",
  amended_approval: "변경승인",
  construction_start: "착공",
  sale_approval_application: "입주자 모집 승인 신청",
  sale_notice: "입주자 모집 공고",
  sale: "분양",
  completion: "준공",
};

export const SCHOOL_EDITION_LABELS = {
  event_unconfirmed: "사업 사건일 미확인",
  edition_unconfirmed: "해당 날짜의 공식 판본 미확인",
  edition_unavailable: "법령 조회 연결 실패",
  edition_conflict: "같은 날짜에 서로 다른 판본 후보가 있습니다",
  edition_verified: "해당 날짜의 공식 판본 확인",
};

export type SchoolEventKind = keyof typeof SCHOOL_EVENT_LABELS;
export type SchoolSiteLawBasis = {
  schemaVersion: "realytics-school-site-law-basis-v1";
  basisHash: string;
  event: {
    status: "unknown" | "actual" | "assumed";
    kind: SchoolEventKind | null;
    date: string | null;
    sourceLocator: { archiveId: string; fileName: string; location: string } | null;
  };
  editionStatus: keyof typeof SCHOOL_EDITION_LABELS;
  edition: {
    title: string;
    officialVersionId: string;
    effectiveFrom: string;
    effectiveTo: string | null;
    contentHash: string;
    url: string;
    provisions: { path: string; contentHash: string; url: string }[];
  } | null;
};
