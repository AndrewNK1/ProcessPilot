// Resource types (human / batch / unassigned) and the evidence schema version,
// turned into display rows. Pure, so vitest covers it without a DOM.

import type { ResourceTypeBreakdown } from "./api";
import { fmtInt, fmtShare } from "./format";

export const TYPE_LABEL: Record<string, string> = {
  human: "People",
  batch: "Batch jobs",
  unassigned: "No user recorded",
};

const BASIS_LABEL: Record<string, string> = {
  pattern: "by pattern",
  values: "listed value",
  default: "default type",
  empty: "empty value",
};

export interface ResourceTypeRow {
  type: string;
  label: string;
  events: string;
  eventShare: string;
  cases: string;
  caseShare: string;
  distinct: string;
  examples: string;
  basis: string;
}

export function resourceTypeRows(b: ResourceTypeBreakdown): ResourceTypeRow[] {
  return b.types.map((t) => ({
    type: t.type,
    label: TYPE_LABEL[t.type] ?? t.type,
    events: fmtInt(t.event_count),
    eventShare: fmtShare(t.event_share),
    cases: fmtInt(t.case_count),
    caseShare: fmtShare(t.case_share),
    distinct: fmtInt(t.distinct_resources),
    examples: t.example_values.length ? t.example_values.join(", ") : "—",
    basis:
      Object.entries(t.events_by_basis)
        .map(([k, n]) => `${BASIS_LABEL[k] ?? k} ${fmtInt(n)}`)
        .join(" · ") || "—",
  }));
}

// The schema history lives in mining/evidence.py; 1 = files written before
// the field existed (every resource value counted as a person).
export const CURRENT_EVIDENCE_SCHEMA = 2;

export function schemaLabel(version: number | null | undefined): string {
  if (version === null || version === undefined) return "evidence schema unknown";
  return version === 1 ? "evidence schema 1 (unversioned)" : `evidence schema ${version}`;
}

export function schemaIsLegacy(version: number | null | undefined): boolean {
  return version !== null && version !== undefined && version < CURRENT_EVIDENCE_SCHEMA;
}
