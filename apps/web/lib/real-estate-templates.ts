import {
  BarChart3,
  Building2,
  Factory,
  FileSearch,
  FileText,
  Home,
  Landmark,
  RefreshCw,
  ShieldCheck,
  Store,
} from "lucide-react";

export type RealEstateTemplateKey =
  | "apartment_pf"
  | "officetel_pf"
  | "logistics_pf"
  | "land_ledger_review"
  | "reit_review"
  | "risk_report"
  | "refinancing"
  | "mixed_use_retail";

export interface RealEstateTemplate {
  key: RealEstateTemplateKey;
  title: string;
  subtitle: string;
  category: string;
  icon: typeof Home;
  purpose: string;
  workflows: string[];
  requiredMaterials: string[];
  outputs: string[];
  validationChecks: string[];
  recommendedFor: string[];
}

export const REAL_ESTATE_TEMPLATES: RealEstateTemplate[] = [
  {
    key: "apartment_pf",
    title: "아파트 PF 심사",
    subtitle: "공동주택 분양형 개발사업",
    category: "분양형 PF",
    icon: Home,
    purpose: "아파트 분양수입을 PF 상환재원으로 사용하는 사업의 사업수지, 분양성, Exit 분양률, 승인조건을 검토합니다.",
    workflows: ["사업수지 분석", "분양성·시장성 분석", "PF 구조·신용보강 검토", "전체 PF 심사"],
    requiredMaterials: ["사업계획서", "건축개요", "사업수지 Excel", "분양가 산정표", "PF term sheet", "토지조서"],
    outputs: ["PF 심사보고서", "Cash Flow Workbook", "분양성 검토 메모", "리스크 등록부", "승인 가능성 대시보드"],
    validationChecks: ["총분양수입 대사", "세대수·면적 대사", "계약금·중도금·잔금 수납스케줄 검증", "Exit 분양률 검증"],
    recommendedFor: ["주상복합 내 공동주택", "일반분양 아파트", "도시형생활주택"],
  },
  {
    key: "officetel_pf",
    title: "오피스텔 PF 심사",
    subtitle: "주거용·업무용 오피스텔",
    category: "분양형 PF",
    icon: Building2,
    purpose: "오피스텔의 전용률, 주차, 임대수익률, 분양가 적정성, 잔금납부 위험을 검토합니다.",
    workflows: ["사업수지 분석", "분양성·시장성 분석", "수익지표·Stress Test", "PF 구조·신용보강 검토"],
    requiredMaterials: ["사업계획서", "상품구성표", "예상 임대료", "인근 오피스텔 실거래·분양사례", "사업수지 Excel"],
    outputs: ["분양성 검토 메모", "가격 비교표", "임대수익률 검증표", "할인분양 Trigger", "PF 심사보고서"],
    validationChecks: ["제안 분양가 기준 검증", "임대료 가정 근거 확인", "전용률·계약면적 검증", "미분양 LTV 검증"],
    recommendedFor: ["역세권 오피스텔", "소형 주거상품", "업무용 오피스텔"],
  },
  {
    key: "logistics_pf",
    title: "물류센터 PF",
    subtitle: "선매입·책임임차 구조",
    category: "운영·매각형 PF",
    icon: Factory,
    purpose: "임대차계약, 선매입계약, Cap Rate, 선매각대금 기반 LTV와 책임준공 구조를 검토합니다.",
    workflows: ["사업수지 분석", "수익지표·Stress Test", "PF 구조·신용보강 검토", "전체 PF 심사"],
    requiredMaterials: ["임대차계약서", "선매입 LOI/SPA", "감정평가서", "공사도급계약", "PF term sheet", "사업수지 Excel"],
    outputs: ["물류센터 Exit 검토", "선매각 LTV 분석", "책임임차 리스크 메모", "PF 심사보고서"],
    validationChecks: ["선매각금액과 PF 상환 대사", "Cap Rate 검증", "임차인 신용도 확인", "준공·거래종결 선행조건 검증"],
    recommendedFor: ["BTS 물류센터", "선매입 약정 사업", "책임임차인 기반 PF"],
  },
  {
    key: "land_ledger_review",
    title: "토지조서 검증",
    subtitle: "PNU·공시지가·등기 증빙",
    category: "토지권원",
    icon: FileSearch,
    purpose: "토지조서 Excel을 파싱하고 필지, 소유자, 지급정보, PNU, 공시지가, 등기부 증빙 누락을 검토합니다.",
    workflows: ["토지권원 검토"],
    requiredMaterials: ["토지조서 Excel", "매매계약서", "공시지가 증빙", "등기부등본", "지적도·토지이용계획"],
    outputs: ["토지 증빙 패키지", "토지취득률 요약", "법무검토 체크리스트", "PF 인출 선행조건 메모"],
    validationChecks: ["필지 수 대사", "면적 합계 대사", "매입금액 대사", "소유자·지분 검증", "제한물권 증빙 확인"],
    recommendedFor: ["브릿지론", "본PF 전환", "토지권원 선행검토"],
  },
  {
    key: "reit_review",
    title: "리츠 편입 검토",
    subtitle: "NOI·Cap Rate·배당가능성",
    category: "투자·운용",
    icon: Landmark,
    purpose: "NOI, Cap Rate, 임대차 안정성, 차입구조, 배당가능성을 중심으로 리츠 편입 가능성을 검토합니다.",
    workflows: ["사업수지 분석", "수익지표·Stress Test"],
    requiredMaterials: ["임대차 현황", "NOI 산정표", "감정평가서", "차입조건", "운영비 내역"],
    outputs: ["리츠 편입 메모", "NOI 검증표", "Cap Rate 민감도", "배당가능성 검토"],
    validationChecks: ["NOI 산정 근거 확인", "공실률 가정 검증", "Cap Rate 출처 확인", "차입금 상환능력 검토"],
    recommendedFor: ["상업용 부동산 편입", "물류센터 편입", "임대주택·오피스 편입"],
  },
  {
    key: "risk_report",
    title: "리스크 보고서",
    subtitle: "Risk Register·승인조건",
    category: "심사보고",
    icon: ShieldCheck,
    purpose: "여러 Agent가 산출한 리스크를 하나의 등록부로 모아 승인, 유보, 보완, 해소 상태를 관리합니다.",
    workflows: ["전체 PF 심사"],
    requiredMaterials: ["PF 심사보고서 초안", "Risk Register Excel", "법무·토지·분양성 증빙", "심사역 의견"],
    outputs: ["리스크 등록부", "승인조건 문안", "Evidence Graph", "감사 추적 패키지"],
    validationChecks: ["리스크별 근거 위치 확인", "review status 확인", "waiver 사유 확인", "승인조건과 리스크 연결"],
    recommendedFor: ["투자심의위원회", "대주단 내부심사", "사후감사 대응"],
  },
  {
    key: "refinancing",
    title: "리파이낸싱 검토",
    subtitle: "준공 후 담보대출·상환재원",
    category: "Exit·상환",
    icon: RefreshCw,
    purpose: "준공 후 담보가치, 미분양 담보대출, Bulk Sale, 선매입, 리파이낸싱 가능성을 검토합니다.",
    workflows: ["수익지표·Stress Test", "PF 구조·신용보강 검토"],
    requiredMaterials: ["준공 후 감정가", "분양률 현황", "미분양 현황", "담보대출 가능조건", "리파이낸싱 term sheet"],
    outputs: ["리파이낸싱 메모", "미분양 LTV Matrix", "상환재원 검토", "대주단 협의 조건표"],
    validationChecks: ["담보가치 출처 확인", "미분양 할인율 검증", "선순위 상환부족 cure 검증", "리파이낸싱 조건 증빙"],
    recommendedFor: ["준공 전후 PF", "미분양 발생 사업", "만기연장·대환 검토"],
  },
  {
    key: "mixed_use_retail",
    title: "상업시설 분양성 검토",
    subtitle: "근린생활시설·단지 내 상가",
    category: "분양형 PF",
    icon: Store,
    purpose: "층별 분양가, 임대료 부담능력, 상권, 공실위험, 미분양 환가가치를 검토합니다.",
    workflows: ["분양성·시장성 분석", "수익지표·Stress Test"],
    requiredMaterials: ["층별 분양가표", "상권분석 자료", "예상 임대료", "주변 공실률", "사업수지 Excel"],
    outputs: ["상가 분양가 검토", "임대료 역산표", "공실 리스크 메모", "할인분양 Trigger"],
    validationChecks: ["층별 가격 차등 검증", "임대료 기반 가격 검증", "공실률 출처 확인", "투자수익률 검증"],
    recommendedFor: ["단지 내 상가", "근린생활시설", "저층부 상업시설"],
  },
];

export function templateByKey(key: string) {
  return REAL_ESTATE_TEMPLATES.find((template) => template.key === key);
}
