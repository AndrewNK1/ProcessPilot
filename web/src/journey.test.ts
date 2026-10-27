import { describe, expect, it } from "vitest";
import type { EvidenceItem, PathWaits, Proposal } from "./api";
import { bottleneckIds, defaultRank, legs, pathForProposal, ticks, touched } from "./journey";
import { durationParts, fmtDuration, fmtShare, shortId } from "./format";

const variant = (rank: number, activities: string[]): EvidenceItem => ({
  id: `VA-${String(rank).padStart(2, "0")}-s`,
  segment: "s",
  type: "variant",
  summary: "",
  metrics: { rank, activities, case_share: 0.1, median_throughput_seconds: 86400 },
  refs: [],
});

const bottleneck = (id: string, source: string, target: string): EvidenceItem => ({
  id,
  segment: "s",
  type: "bottleneck",
  summary: "",
  metrics: { source, target },
  refs: [],
});

const waits: PathWaits = {
  evidence_id: "VA-02-s",
  activities: ["A", "B", "C"],
  case_count: 4,
  mean_throughput_seconds: 300,
  median_throughput_seconds: 280,
  steps: [
    { position: 0, activity: "A", mean_wait_seconds: null, median_wait_seconds: null, p90_wait_seconds: null },
    { position: 1, activity: "B", mean_wait_seconds: 100, median_wait_seconds: 90, p90_wait_seconds: 150 },
    { position: 2, activity: "C", mean_wait_seconds: 200, median_wait_seconds: 190, p90_wait_seconds: 260 },
  ],
};

const proposal = (ops: Proposal["edit_operations"]): Proposal => ({
  title: "p",
  heuristic_id: "H",
  edit_operations: ops,
  evidence_ids: [],
  rationale: "",
  expected_effect: "",
  risks: [],
  assumptions: [],
});

describe("itinerary", () => {
  it("defaults to the most frequent path with at least two waits", () => {
    expect(defaultRank([variant(1, ["A", "B"]), variant(2, ["A", "B", "C"])])).toBe(2);
    expect(defaultRank([variant(1, ["A", "B"])])).toBe(1);
  });

  it("tags the leg a ranked bottleneck leads into", () => {
    const rows = legs(waits, bottleneckIds([bottleneck("BN-01-s", "B", "C")]));
    expect(rows.map((r) => r.bottleneckId)).toEqual([null, null, "BN-01-s"]);
    expect(rows[0].meanSeconds).toBeNull();
  });

  it("marks a proposal's activities and inserts added steps after their anchor", () => {
    const { marks, added } = touched(
      proposal([
        { op: "automate_activity", activity: "B" },
        { op: "add_check", name: "Check", after: "B" },
      ]),
    );
    const rows = legs(waits, new Map(), marks, added);
    expect(rows.map((r) => [r.activity, r.mark])).toEqual([
      ["A", null],
      ["B", "automated"],
      ["Check", "added"],
      ["C", null],
    ]);
  });

  it("picks the most frequent path the proposal touches", () => {
    const vs = [variant(1, ["A", "B"]), variant(2, ["A", "C"])];
    expect(pathForProposal(vs, proposal([{ op: "remove_activity", activity: "C" }]))?.metrics.rank).toBe(2);
    expect(pathForProposal(vs, proposal([{ op: "remove_activity", activity: "Z" }]))).toBeNull();
  });

  it("puts round ticks from zero to just above the maximum", () => {
    const t = ticks(23.1 * 86400);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(23.1 * 86400);
    expect(t.every((v) => (v / 86400) % 1 === 0 || (v / 86400) % 0.5 === 0)).toBe(true);
  });
});

describe("format", () => {
  it("formats durations like the evidence summaries", () => {
    expect(durationParts(3.5 * 86400)).toEqual(["3.5", "d"]);
    expect(fmtDuration(4 * 3600)).toBe("4.0 h");
    expect(fmtDuration(12 * 60)).toBe("12 min");
    expect(fmtDuration(40)).toBe("40 s");
    expect(fmtDuration(null)).toBe("—");
    expect(fmtShare(0.123)).toBe("12.3%");
  });

  it("shortens evidence IDs to their type and rank", () => {
    expect(shortId("BN-01-2-way-match")).toBe("BN-01");
    expect(shortId("CF-R-02-consignment")).toBe("CF-R-02");
    expect(shortId("KPI-01-3-way-match-invoice-after-gr")).toBe("KPI-01");
  });
});
