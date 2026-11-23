export type AreaSourceLocator = {
  archiveId?: string;
  fileName?: string;
  location?: string;
  sheetName?: string;
  cell?: string;
  rowNumber?: number;
};

export type ProgramAreaBasis = {
  land?: {
    kind: "parcel_estimate" | "site_estimate";
    status: "estimated" | "unconfirmed";
    amountKrw: number | null;
    includedAreaSqm: number | null;
    unitPriceKrw?: number | null;
    parcelSetHash?: string | null;
    parcels?: Array<{
      parcelIndex: number;
      pnu: string | null;
      excluded: boolean;
      ledgerAreaSqm?: number | null;
      includedAreaSqm: number | null;
      unitPriceKrw: number | null;
      estimatedCostKrw: number | null;
      sourceLocator?: AreaSourceLocator;
    }>;
  };
  construction?: {
    kind: "declared_area" | "drawing";
    status: "estimated" | "unconfirmed";
    grossFloorAreaSqm: number | null;
    unitPriceKrw: number;
    amountKrw: number | null;
    revision?: string | null;
    sourceLocator?: AreaSourceLocator | null;
  };
};

export type AreaDrawing = { grossFloorAreaSqm: number; archiveId: string; revision: string; location: string };
