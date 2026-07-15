import { NextResponse } from "next/server";

const MOLIT_BASE = "https://apis.data.go.kr/1613000/arLandUseInfoService";

function sourceUnavailable(pnu: string, reason: string, message: string, status = 503) {
  return NextResponse.json(
    {
      pnu,
      dataStatus: "source_unavailable",
      source: "molit",
      sourceUnavailable: true,
      evidenceEligible: false,
      reason,
      message,
    },
    { status },
  );
}

function getXml(xml: string, tag: string): string {
  const match = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`));
  return match ? match[1]!.trim() : "";
}

function getXmlAll(xml: string, tag: string): string[] {
  const regex = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, "g");
  const results: string[] = [];
  let match = regex.exec(xml);

  while (match) {
    results.push(match[1]!.trim());
    match = regex.exec(xml);
  }

  return results;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const pnu = searchParams.get("pnu");

  if (!pnu || pnu.length !== 19) {
    return NextResponse.json(
      {
        error: "PNU는 19자리여야 합니다.",
      },
      { status: 400 },
    );
  }

  const apiKey = process.env.MOLIT_API_KEY;

  if (!apiKey) {
    return sourceUnavailable(
      pnu,
      "missing_configuration",
      "MOLIT_API_KEY가 설정되지 않아 PNU 토지이용계획 자료를 조회할 수 없습니다.",
    );
  }

  try {
    const params = new URLSearchParams({
      serviceKey: apiKey,
      pnu,
      numOfRows: "20",
      pageNo: "1",
    });
    const response = await fetch(`${MOLIT_BASE}/DTarLandUseInfo?${params.toString()}`, {
      headers: { Accept: "application/xml", "User-Agent": "REALYTICS-Platform9" },
      next: { revalidate: 3600 },
    });
    const xml = await response.text();
    const resultCode = getXml(xml, "resultCode");

    if (resultCode && resultCode !== "00") {
      return sourceUnavailable(
        pnu,
        `upstream_result_${resultCode}`,
        `국토교통부 토지이용계획 API 오류: ${getXml(xml, "resultMsg") || "응답 코드 확인 필요"}`,
        502,
      );
    }

    const zoneItems = getXmlAll(xml, "jiyukjiguItem");
    const zones = zoneItems.map((item) => ({
      gb: getXml(item, "jiyukjiguGbCd"),
      code: getXml(item, "jiyukjiguCd"),
      name: getXml(item, "jiyukjiguNm"),
      detail: getXml(item, "ladUsePlnSpfc"),
    }));
    const primaryZone = zones.find((zone) => zone.gb === "용도지역") ?? zones[0] ?? null;

    return NextResponse.json({
      pnu,
      address: getXml(xml, "lAddr") || "주소 정보 없음",
      jimok: getXml(xml, "jimok") || "-",
      jimokCd: getXml(xml, "jimokCd"),
      area: getXml(xml, "lndcgrAr") ? Number(getXml(xml, "lndcgrAr")) : null,
      zones,
      primaryZone,
      rawCount: zoneItems.length,
      source: "molit",
      dataStatus: "available",
      sourceUnavailable: false,
      evidenceEligible: true,
    });
  } catch (error) {
    console.error("PNU lookup error:", error);
    return sourceUnavailable(
      pnu,
      "upstream_request_failed",
      "국토교통부 토지이용계획 API 요청에 실패했습니다. 가상 토지자료는 생성하지 않았습니다.",
      502,
    );
  }
}
