import { describe, expect, it } from "vitest";
import type { ResourceTypeBreakdown } from "./api";
import { resourceTypeRows, schemaIsLegacy, schemaLabel } from "./resourceTypes";

const breakdown: ResourceTypeBreakdown = {
  configured: true,
  rule_text: "human = values matching ^user_\\d+$; anything else is human",
  event_count: 15,
  case_count: 5,
  defaulted_event_count: 0,
  defaulted_values: [],
  types: [
    {
      type: "human",
      event_count: 9,
      event_share: 0.6,
      case_count: 5,
      case_share: 1,
      distinct_resources: 2,
      example_values: ["u1", "u2"],
      events_by_basis: { pattern: 9 },
    },
    {
      type: "unassigned",
      event_count: 2,
      event_share: 2 / 15,
      case_count: 2,
      case_share: 0.4,
      distinct_resources: 2,
      example_values: ["(empty)", "NONE"],
      events_by_basis: { values: 1, empty: 1 },
    },
    {
      type: "batch",
      event_count: 0,
      event_share: 0,
      case_count: 0,
      case_share: 0,
      distinct_resources: 0,
      example_values: [],
      events_by_basis: {},
    },
  ],
};

describe("resource types", () => {
  it("labels types in analyst words and keeps the server's order", () => {
    const rows = resourceTypeRows(breakdown);
    expect(rows.map((r) => r.label)).toEqual(["People", "No user recorded", "Batch jobs"]);
    expect(rows[1]).toMatchObject({ events: "2", eventShare: "13.3%", caseShare: "40.0%" });
    expect(rows[1].basis).toBe("listed value 1 · empty value 1");
    expect(rows[1].examples).toBe("(empty), NONE");
    expect(rows[2]).toMatchObject({ examples: "—", basis: "—" });
  });

  it("names the evidence schema and flags runs before resource types", () => {
    expect(schemaLabel(1)).toBe("evidence schema 1 (unversioned)");
    expect(schemaLabel(2)).toBe("evidence schema 2");
    expect(schemaLabel(null)).toBe("evidence schema unknown");
    expect(schemaIsLegacy(1)).toBe(true);
    expect(schemaIsLegacy(2)).toBe(false);
    expect(schemaIsLegacy(null)).toBe(false);
  });
});
