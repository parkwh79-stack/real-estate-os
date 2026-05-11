import { NextResponse } from "next/server";

const MOLIT_BASE = "https://apis.data.go.kr/1613000/arLandUseInfoService";

function mockPnuResponse(pnu: string, source = "mock") {
  return {
    pnu,
    address: "대구광역시 달서구 본동 743 일원",
    jimok: "대",
    jimokCd: "08",
    area: 2586.8,
    zones: [
      { gb: "용도지역", code: "UQA123", name: "일반상업지역", detail: "도시지역" },
      { gb: "기타", code: "UQA999", name: "지구단위계획구역", detail: "사전협의 필요" },
    ],
    primaryZone: { gb: "용도지역", code: "UQA123", name: "일반상업지역" },
    rawCount: 2,
    source,
  };
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
    return NextResponse.json(mockPnuResponse(pnu));
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
      return NextResponse.json(
        { error: `MOLIT API 오류: ${getXml(xml, "resultMsg")}` },
        { status: 502 },
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
    });
  } catch (error) {
    console.error("PNU lookup error:", error);
    return NextResponse.json(mockPnuResponse(pnu, "mock_fallback"));
  }
}
