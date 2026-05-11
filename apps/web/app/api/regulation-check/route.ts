import { NextResponse } from "next/server";

const LAW_API = "https://www.law.go.kr/DRF";

interface ZoneRule {
  maxFar: number;
  stdFar: number;
  allowFar: number;
  maxBcr: number;
  law: string;
}

const zoneRules: Record<string, ZoneRule> = {
  일반상업지역: {
    maxFar: 1500,
    stdFar: 600,
    allowFar: 800,
    maxBcr: 60,
    law: "국토의 계획 및 이용에 관한 법률 시행령 별표 8",
  },
  중심상업지역: {
    maxFar: 1500,
    stdFar: 800,
    allowFar: 1000,
    maxBcr: 60,
    law: "국토의 계획 및 이용에 관한 법률 시행령 별표 8",
  },
  근린상업지역: {
    maxFar: 900,
    stdFar: 400,
    allowFar: 600,
    maxBcr: 60,
    law: "국토의 계획 및 이용에 관한 법률 시행령 별표 8",
  },
  준주거지역: {
    maxFar: 500,
    stdFar: 300,
    allowFar: 400,
    maxBcr: 60,
    law: "국토의 계획 및 이용에 관한 법률 시행령 별표 6",
  },
};

interface LawSearchResponse {
  LawSearch?: {
    law?: Array<{
      법령명한글?: string;
      법령일련번호?: string;
      시행일자?: string;
    }>;
  };
}

export async function POST(request: Request) {
  const body = (await request.json()) as {
    zone?: string;
    plannedFar?: number;
    plannedBcr?: number;
    address?: string;
  };

  const zone = body.zone ?? "일반상업지역";
  const plannedFar = Number(body.plannedFar ?? 0);
  const plannedBcr = Number(body.plannedBcr ?? 0);
  const rule = zoneRules[zone] ?? zoneRules["일반상업지역"]!;
  const farOk = plannedFar <= rule.allowFar;
  const bcrOk = plannedBcr <= rule.maxBcr;
  const status = farOk && bcrOk ? "compliant" : farOk ? "review_needed" : "non_compliant";

  const lawResult = await lookupLaw();

  const checklist = [
    {
      item: `용도지역 용적률 기준 (${zone})`,
      ok: farOk,
      detail: `계획 ${plannedFar}% · 허용 ${rule.allowFar}% · 법정 상한 ${rule.maxFar}%`,
      law: rule.law,
    },
    {
      item: "건폐율 기준",
      ok: bcrOk,
      detail: `계획 ${plannedBcr}% · 최대 ${rule.maxBcr}%`,
      law: "국토의 계획 및 이용에 관한 법률 시행령",
    },
    {
      item: "지구단위계획 추가 제한",
      ok: null,
      detail: "관할 구청 건축과 사전협의 및 공개공지, 기부채납 조건 확인 필요",
      law: "국토계획법 및 지자체 도시계획조례",
    },
    {
      item: "건축위원회 심의 여부",
      ok: null,
      detail: "연면적, 용도, 층수 조건에 따라 건축심의 대상 여부 확인 필요",
      law: "건축법 및 지방건축위원회 운영기준",
    },
  ];

  const summary =
    farOk && bcrOk
      ? `${body.address ?? zone} 계획안은 기본 용적률과 건폐율 기준을 충족합니다. 다만 지구단위계획구역, 공개공지, 교통영향평가 조건은 관할기관 협의 결과에 따라 사업성이 달라질 수 있습니다.`
      : `${body.address ?? zone} 계획안은 일부 기준 초과 또는 추가 협의가 필요합니다. 인센티브 적용 가능성, 층수 제한, 공개공지 제공 조건을 재검토해야 합니다.`;

  return NextResponse.json({
    status,
    zone,
    rule,
    plannedFar,
    plannedBcr,
    farOk,
    bcrOk,
    checklist,
    summary,
    lawResult,
    hasRealApi: Boolean(process.env.LAW_OC_KEY),
  });
}

async function lookupLaw() {
  const oc = process.env.LAW_OC_KEY;

  if (!oc) {
    return {
      name: "국토의 계획 및 이용에 관한 법률",
      msn: "mock",
      efdt: "sample",
    };
  }

  try {
    const searchUrl = `${LAW_API}/lawSearch.do?OC=${oc}&target=law&query=${encodeURIComponent("국토의 계획 및 이용에 관한 법률")}&type=JSON&display=1`;
    const response = await fetch(searchUrl, {
      headers: { "User-Agent": "REALYTICS-Platform9" },
      next: { revalidate: 86400 },
    });

    if (!response.ok) {
      return null;
    }

    const data = (await response.json()) as LawSearchResponse;
    const first = data.LawSearch?.law?.[0];

    return first
      ? {
          name: first.법령명한글,
          msn: first.법령일련번호,
          efdt: first.시행일자,
        }
      : null;
  } catch {
    return null;
  }
}
