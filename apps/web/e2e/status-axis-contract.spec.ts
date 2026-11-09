import { expect, test } from "@playwright/test";
import {
  normalizeExecutionStatus,
  projectExecutionStatus,
} from "@repo/ui/status-axis";

test("preserves canonical Parser V2 execution states", () => {
  expect(projectExecutionStatus("parser_v2", "completed_with_warnings")).toEqual({
    status: "completed_with_warnings",
    rawStatus: "completed_with_warnings",
    mappingCode: "EXACT",
  });
  expect(projectExecutionStatus("parser_v2", "blocked")).toEqual({
    status: "unknown",
    rawStatus: "blocked",
    mappingCode: "UNKNOWN_SOURCE_STATUS",
  });
});

test("projects recent-work technical states with explicit mapping reasons", () => {
  expect(projectExecutionStatus("recent_work_v2", "queued")).toEqual({
    status: "pending",
    rawStatus: "queued",
    mappingCode: "QUEUED_AS_PENDING",
  });
  expect(projectExecutionStatus("recent_work_v2", "retrying")).toEqual({
    status: "running",
    rawStatus: "retrying",
    mappingCode: "RETRYING_AS_RUNNING",
  });
  expect(projectExecutionStatus("recent_work_v2", "completed_with_blockers")).toEqual({
    status: "completed_with_warnings",
    rawStatus: "completed_with_blockers",
    mappingCode: "COMPLETED_WITH_BLOCKERS_AS_WARNING",
  });
  expect(projectExecutionStatus("recent_work_v2", "needs_recalculation")).toEqual({
    status: "completed_with_warnings",
    rawStatus: "needs_recalculation",
    mappingCode: "RECALCULATION_REQUIRED",
  });
});

test("does not infer missing or unknown technical states as success", () => {
  expect(projectExecutionStatus("recent_work_v2", undefined)).toEqual({
    status: "unknown",
    rawStatus: null,
    mappingCode: "NO_EXECUTION_RECORD",
  });
  expect(projectExecutionStatus("recent_work_v2", "published")).toEqual({
    status: "unknown",
    rawStatus: "published",
    mappingCode: "UNKNOWN_SOURCE_STATUS",
  });
  expect(projectExecutionStatus("recent_work_v2", "waiting_for_human")).toEqual({
    status: "unknown",
    rawStatus: "waiting_for_human",
    mappingCode: "UNKNOWN_SOURCE_STATUS",
  });
});

test("keeps the legacy canonical normalizer contract unchanged", () => {
  expect(normalizeExecutionStatus("queued")).toBe("unknown");
  expect(normalizeExecutionStatus("completed")).toBe("completed");
});
