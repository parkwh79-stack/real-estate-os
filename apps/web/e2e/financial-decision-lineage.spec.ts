import { expect, test } from "@playwright/test";
import {
  projectChiefArtifactResult,
  resolveFinancialDecisionBinding,
  selectChiefPodArtifact,
  type ChiefPodArtifactRecord,
} from "../lib/financial-decision-package";

const DECISION_LINEAGE = {
  runId: "run-pf-2026-07-27",
  snapshotId: "snapshot-run-pf-2026-07-27",
  snapshotHash: `sha256:${"a".repeat(64)}`,
};

test("binds a calculation only to the exact run and immutable snapshot", () => {
  expect(
    resolveFinancialDecisionBinding(
      { ...DECISION_LINEAGE },
      { ...DECISION_LINEAGE },
    ),
  ).toEqual({
    status: "bound",
    ...DECISION_LINEAGE,
    issues: [],
  });
});

test("fails closed when any required lineage value is missing", () => {
  const binding = resolveFinancialDecisionBinding(
    {
      runId: DECISION_LINEAGE.runId,
      snapshotId: DECISION_LINEAGE.snapshotId,
    },
    DECISION_LINEAGE,
  );

  expect(binding.status).toBe("unbound");
  expect(binding).toMatchObject({
    runId: null,
    snapshotId: null,
    snapshotHash: null,
    issues: [
      {
        field: "snapshotHash",
        reason: "missing",
        calculationValue: null,
        decisionValue: DECISION_LINEAGE.snapshotHash,
      },
    ],
  });
});

test("fails closed for each run, snapshot, or hash mismatch", () => {
  for (const [field, value] of [
    ["runId", "run-pf-other"],
    ["snapshotId", "snapshot-other"],
    ["snapshotHash", `sha256:${"b".repeat(64)}`],
  ] as const) {
    const binding = resolveFinancialDecisionBinding(
      { ...DECISION_LINEAGE, [field]: value },
      DECISION_LINEAGE,
    );
    expect(binding.status).toBe("unbound");
    expect(binding.issues).toContainEqual(
      expect.objectContaining({ field, reason: "mismatch" }),
    );
  }
});

test("selects only the explicitly requested Chief artifact without a latest fallback", () => {
  const records: ChiefPodArtifactRecord[] = [
    {
      artifactKey: "artifact:pf_credit:newer",
      podKey: "pf_credit",
      createdAt: "2026-07-27T02:00:00Z",
      result: { creditReviewDecision: { decision: "hold" } },
    },
    {
      artifactKey: "artifact:pf_credit:approved-input",
      podKey: "pf_credit",
      createdAt: "2026-07-27T01:00:00Z",
      result: { creditReviewDecision: { decision: "conditional" } },
    },
  ];

  const selected = selectChiefPodArtifact(records, {
    artifactKey: "artifact:pf_credit:approved-input",
    podKey: "pf_credit",
  });
  expect(selected?.artifactKey).toBe("artifact:pf_credit:approved-input");
  expect(
    selectChiefPodArtifact(records, {
      artifactKey: "artifact:pf_credit:missing",
      podKey: "pf_credit",
    }),
  ).toBeNull();
  expect(
    selectChiefPodArtifact(records, {
      artifactKey: "artifact:pf_credit:approved-input",
      podKey: "feasibility_cashflow",
    }),
  ).toBeNull();
});

test("projects every PF decision field from the same selected artifact", () => {
  const records: ChiefPodArtifactRecord[] = [
    {
      artifactKey: "artifact:pf_credit:newer",
      podKey: "pf_credit",
      result: {
        pfStructure: { package: "newer" },
        riskRegister: { package: "newer" },
        creditReviewDecision: { package: "newer" },
        covenantEod: { package: "newer" },
      },
    },
    {
      artifactKey: "artifact:pf_credit:target",
      podKey: "pf_credit",
      calculationRunIds: ["calc:pf:target", "calc:pf:target", ""],
      inputHash: "input-target",
      outputHash: "output-target",
      result: {
        pfStructure: { package: "target" },
        riskRegister: { package: "target" },
        creditReviewDecision: { package: "target" },
        covenantEod: { package: "target" },
      },
    },
  ];
  const selected = selectChiefPodArtifact(records, {
    artifactKey: "artifact:pf_credit:target",
    podKey: "pf_credit",
  });
  const projection = projectChiefArtifactResult(selected, [
    "pfStructure",
    "riskRegister",
    "creditReviewDecision",
    "covenantEod",
    "missingField",
  ] as const);

  expect(projection).toEqual({
    artifactKey: "artifact:pf_credit:target",
    podKey: "pf_credit",
    calculationRunIds: ["calc:pf:target"],
    inputHash: "input-target",
    outputHash: "output-target",
    values: {
      pfStructure: { package: "target" },
      riskRegister: { package: "target" },
      creditReviewDecision: { package: "target" },
      covenantEod: { package: "target" },
      missingField: undefined,
    },
    missingFields: ["missingField"],
  });
});

test("rejects an ambiguous duplicated Chief artifact identity", () => {
  const duplicate: ChiefPodArtifactRecord = {
    artifactKey: "artifact:pf_credit:duplicate",
    podKey: "pf_credit",
    result: { pfStructure: {} },
  };

  expect(
    selectChiefPodArtifact([duplicate, { ...duplicate }], {
      artifactKey: "artifact:pf_credit:duplicate",
      podKey: "pf_credit",
    }),
  ).toBeNull();
});
