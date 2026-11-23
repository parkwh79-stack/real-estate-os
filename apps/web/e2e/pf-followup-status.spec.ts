import { expect, test } from "@playwright/test";
import { pfFollowupStates, readPfFollowupExecution } from "../lib/pf-followup-status";
import { fetchCashFlowDependentRuns, type CashflowResult } from "../lib/project-client";

const cashflow: CashflowResult = {
  runId: "anchor", source: "pf_review_orchestration", monthly: [], summary: {},
  maxMinusEok: null, maxMinusMonth: null, maxMinusPeriodIndex: null,
  inputSnapshot: { financialInputHash: "exact-input" },
};

const waterfall = {
  calculationRunId: "child", engineKey: "waterfall", status: "completed",
  inputSnapshot: { parentCalculationRunId: "anchor", financialInputHash: "exact-input" },
};

test("labels specific PF follow-up outcomes and prefers a proven completed child", () => {
  const execution = readPfFollowupExecution({
    calculationRunId: "anchor", financialInputHash: "exact-input", dependentFailures: [
      { engines: ["waterfall", "tranche_recovery"], status: "failed", reason: "dependent_scenario_failed" },
      { engines: ["unsold_ltv"], status: "needs_input", missingFields: ["productMix", "internal_secret"] },
      { engines: ["discount_stress"], status: "not_supported", reason: "cash_flow_stress_period_unconfirmed" },
    ],
  });
  const states = pfFollowupStates(cashflow, { waterfall }, execution);
  expect(states.map(({ status }) => status)).toEqual(["completed", "failed", "needs_input", "needs_input"]);
  expect(states[0]?.run?.calculationRunId).toBe("child");
  expect(states[2]?.missingFields).toEqual(["productMix"]);
  expect(states[3]?.scheduleNeeded).toBe(true);
});

test("ignores response and child results from a different parent or input snapshot", () => {
  const execution = readPfFollowupExecution({
    calculationRunId: "anchor", financialInputHash: "old-input",
    dependentFailures: [{ engines: ["tranche_recovery"], status: "failed" }],
  });
  const stale = { ...waterfall, inputSnapshot: { ...waterfall.inputSnapshot, financialInputHash: "old-input" } };
  expect(pfFollowupStates(cashflow, { waterfall: stale }, execution, ["waterfall"]).map(({ status }) => status))
    .toEqual(["unavailable", "unknown", "unknown", "unknown"]);
  expect(pfFollowupStates({ ...cashflow, runId: "other-anchor" }, { waterfall }, execution).map(({ status }) => status))
    .toEqual(["unknown", "unknown", "unknown", "unknown"]);
});

test("recovers only known policy and missing data after reopening a saved run", () => {
  const missing = { ...cashflow,
    inputSnapshot: { ...cashflow.inputSnapshot, pfStructure: [] },
    periodBasis: { schemaVersion: "cash-flow-period-basis-v1", kind: "source_sequence" },
  } as const;
  expect(pfFollowupStates(missing).map(({ status }) => status)).toEqual(Array(4).fill("needs_input"));
  expect(pfFollowupStates(missing)[0]?.missingFields).toEqual(["pfStructure"]);
  expect(pfFollowupStates(missing)[3]?.scheduleNeeded).toBe(true);
  expect(pfFollowupStates({ ...cashflow, monthlyFinancing: {} }, { waterfall }).map(({ status }) => status))
    .toEqual(Array(4).fill("unsupported"));
});

test("chooses an exact saved child behind an unrelated run and keeps registry errors separate", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (input) => {
      const url = new URL(String(input), "https://realytics.invalid");
      const engine = url.searchParams.get("engine_key");
      if (engine === "unsold_ltv") return new Response(null, { status: 503 });
      return Response.json({ records: engine === "waterfall"
        ? [{ ...waterfall, inputSnapshot: { ...waterfall.inputSnapshot, parentCalculationRunId: "unrelated" } }, waterfall]
        : [] });
    };
    const { runs, unavailable } = await fetchCashFlowDependentRuns("project", cashflow);
    expect(runs.waterfall).toEqual(waterfall);
    expect(unavailable).toEqual(["unsold_ltv"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
