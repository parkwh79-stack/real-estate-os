/**
 * Fail-closed helpers for financial decision surfaces.
 *
 * A calculation may inherit a decision only when the calculation and decision
 * explicitly name the same orchestration run and immutable snapshot. Timestamps,
 * list order, and "latest" records are intentionally not accepted as lineage.
 */

export interface FinancialDecisionLineage {
  runId?: string | null;
  snapshotId?: string | null;
  snapshotHash?: string | null;
}

export type FinancialDecisionLineageField = keyof Required<FinancialDecisionLineage>;

export interface FinancialDecisionBindingIssue {
  field: FinancialDecisionLineageField;
  reason: "missing" | "mismatch";
  calculationValue: string | null;
  decisionValue: string | null;
}

export type FinancialDecisionBinding =
  | {
      status: "bound";
      runId: string;
      snapshotId: string;
      snapshotHash: string;
      issues: [];
    }
  | {
      status: "unbound";
      runId: null;
      snapshotId: null;
      snapshotHash: null;
      issues: FinancialDecisionBindingIssue[];
    };

const LINEAGE_FIELDS = ["runId", "snapshotId", "snapshotHash"] as const;

function explicitIdentifier(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

/**
 * Resolve whether one calculation is covered by one decision package.
 *
 * Missing lineage is not inferred from a nearby timestamp or a latest record.
 * Every canonical field must be present on both sides and compare exactly.
 */
export function resolveFinancialDecisionBinding(
  calculation: FinancialDecisionLineage | null | undefined,
  decision: FinancialDecisionLineage | null | undefined,
): FinancialDecisionBinding {
  const issues: FinancialDecisionBindingIssue[] = [];

  for (const field of LINEAGE_FIELDS) {
    const calculationValue = explicitIdentifier(calculation?.[field]);
    const decisionValue = explicitIdentifier(decision?.[field]);
    if (calculationValue === null || decisionValue === null) {
      issues.push({
        field,
        reason: "missing",
        calculationValue,
        decisionValue,
      });
    } else if (calculationValue !== decisionValue) {
      issues.push({
        field,
        reason: "mismatch",
        calculationValue,
        decisionValue,
      });
    }
  }

  if (issues.length) {
    return {
      status: "unbound",
      runId: null,
      snapshotId: null,
      snapshotHash: null,
      issues,
    };
  }

  return {
    status: "bound",
    runId: explicitIdentifier(calculation?.runId) as string,
    snapshotId: explicitIdentifier(calculation?.snapshotId) as string,
    snapshotHash: explicitIdentifier(calculation?.snapshotHash) as string,
    issues: [],
  };
}

export interface ChiefPodArtifactRecord<
  TResult extends Record<string, unknown> = Record<string, unknown>,
> {
  artifactKey?: string | null;
  podKey?: string | null;
  status?: string | null;
  calculationRunIds?: string[] | null;
  inputHash?: string | null;
  outputHash?: string | null;
  result?: TResult | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  [key: string]: unknown;
}

export interface ChiefPodArtifactSelection {
  artifactKey: string;
  podKey: string;
}

/**
 * Select exactly one requested Chief Pod artifact.
 *
 * There is deliberately no latest-record fallback. Missing, wrong-pod, or
 * duplicate identities are ambiguous and therefore return null.
 */
export function selectChiefPodArtifact<
  TResult extends Record<string, unknown> = Record<string, unknown>,
>(
  records: readonly ChiefPodArtifactRecord<TResult>[],
  selection: ChiefPodArtifactSelection,
): ChiefPodArtifactRecord<TResult> | null {
  const artifactKey = explicitIdentifier(selection.artifactKey);
  const podKey = explicitIdentifier(selection.podKey);
  if (!artifactKey || !podKey) return null;

  const matches = records.filter(
    (record) =>
      explicitIdentifier(record.artifactKey) === artifactKey &&
      explicitIdentifier(record.podKey) === podKey,
  );
  return matches.length === 1 ? matches[0] ?? null : null;
}

export interface ChiefArtifactResultProjection<TKey extends string> {
  artifactKey: string;
  podKey: string;
  calculationRunIds: string[];
  inputHash: string | null;
  outputHash: string | null;
  values: Record<TKey, unknown>;
  missingFields: TKey[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Project requested fields from one artifact result only.
 *
 * Missing fields remain explicitly missing. Callers must not fill them from a
 * different artifact or a mutable project projection.
 */
export function projectChiefArtifactResult<TKey extends string>(
  artifact: ChiefPodArtifactRecord | null | undefined,
  fieldKeys: readonly TKey[],
): ChiefArtifactResultProjection<TKey> | null {
  const artifactKey = explicitIdentifier(artifact?.artifactKey);
  const podKey = explicitIdentifier(artifact?.podKey);
  if (!artifactKey || !podKey || !isRecord(artifact?.result)) return null;

  const values = {} as Record<TKey, unknown>;
  const missingFields: TKey[] = [];
  for (const field of fieldKeys) {
    if (Object.prototype.hasOwnProperty.call(artifact.result, field)) {
      values[field] = artifact.result[field];
    } else {
      values[field] = undefined;
      missingFields.push(field);
    }
  }

  const calculationRunIds = Array.from(
    new Set(
      (artifact.calculationRunIds ?? [])
        .map((value) => explicitIdentifier(value))
        .filter((value): value is string => value !== null),
    ),
  );

  return {
    artifactKey,
    podKey,
    calculationRunIds,
    inputHash: explicitIdentifier(artifact.inputHash),
    outputHash: explicitIdentifier(artifact.outputHash),
    values,
    missingFields,
  };
}
