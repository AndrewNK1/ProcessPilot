// A case path read as an itinerary: each step is a stop, and the wait before it
// is the layover. Waits come from the API (`mining.paths.path_waits`): the mean
// waits of one path add up to that path's mean throughput, so the itinerary's
// total is a figure the analyst can check against the path's evidence item.
// This module only arranges and labels; it computes no metric.

import type { EditOperation, EvidenceItem, PathWaits, Proposal } from "./api";

export type Mark = "automated" | "removed" | "moved" | "parallel" | "merged" | "added";

export interface Leg {
  activity: string;
  meanSeconds: number | null;
  medianSeconds: number | null;
  p90Seconds: number | null;
  /** BN evidence ID of the transition into this step, when it is a ranked bottleneck. */
  bottleneckId: string | null;
  mark: Mark | null;
}

/** Variant evidence items that carry a path, in rank order. */
export function paths(items: EvidenceItem[]): EvidenceItem[] {
  return items
    .filter((i) => i.type === "variant" && Array.isArray(i.metrics.activities))
    .sort((a, b) => a.metrics.rank - b.metrics.rank);
}

/**
 * The most frequent listed path with at least two waits, else the most frequent one.
 *
 * WHY not always path 1: the happy path is often two steps (40 min in BPI's
 * 2-way match), an itinerary with one layover; frequency keeps the default
 * about what most cases do.
 */
export function defaultRank(variants: EvidenceItem[]): number {
  const long = variants.find((v) => v.metrics.activities.length >= 3);
  return (long ?? variants[0])?.metrics.rank ?? 1;
}

/** Transition -> BN evidence ID, for the bottlenecks that carry a source and target. */
export function bottleneckIds(items: EvidenceItem[]): Map<string, string> {
  const ids = new Map<string, string>();
  for (const item of items) {
    if (item.type === "bottleneck" && item.metrics.source !== undefined) {
      ids.set(edgeKey(item.metrics.source, item.metrics.target), item.id);
    }
  }
  return ids;
}

export const edgeKey = (source: string, target: string) => `${source}\u0000${target}`;

/** What a proposal does to each activity it names, and the steps it inserts (anchor -> name). */
export function touched(proposal: Proposal): { marks: Map<string, Mark>; added: Map<string, string> } {
  const marks = new Map<string, Mark>();
  const added = new Map<string, string>();
  for (const op of proposal.edit_operations as EditOperation[]) {
    switch (op.op) {
      case "automate_activity":
        marks.set(op.activity, "automated");
        break;
      case "remove_activity":
        marks.set(op.activity, "removed");
        break;
      case "add_activity":
      case "add_check":
        added.set(op.after, op.name);
        break;
      case "reorder_activities":
        if (!marks.has(op.activity)) marks.set(op.activity, "moved");
        break;
      case "parallelize_activities":
      case "merge_activities":
        for (const a of op.activities) {
          if (!marks.has(a)) marks.set(a, op.op === "parallelize_activities" ? "parallel" : "merged");
        }
        break;
    }
  }
  return { marks, added };
}

/** The legs of one path, with bottleneck IDs and (for a proposal) its marks and inserted steps. */
export function legs(
  waits: PathWaits,
  bnIds: Map<string, string>,
  marks: Map<string, Mark> = new Map(),
  added: Map<string, string> = new Map(),
): Leg[] {
  const out: Leg[] = [];
  let previous: string | null = null;
  for (const step of waits.steps) {
    out.push({
      activity: step.activity,
      meanSeconds: step.mean_wait_seconds,
      medianSeconds: step.median_wait_seconds,
      p90Seconds: step.p90_wait_seconds,
      bottleneckId: previous === null ? null : bnIds.get(edgeKey(previous, step.activity)) ?? null,
      mark: marks.get(step.activity) ?? null,
    });
    const insert = added.get(step.activity);
    if (insert) {
      out.push({
        activity: insert,
        meanSeconds: null,
        medianSeconds: null,
        p90Seconds: null,
        bottleneckId: null,
        mark: "added",
      });
    }
    previous = step.activity;
  }
  return out;
}

/** The most frequent listed path that passes any activity the proposal touches. */
export function pathForProposal(variants: EvidenceItem[], proposal: Proposal): EvidenceItem | null {
  const { marks, added } = touched(proposal);
  const names = new Set([...marks.keys(), ...added.keys()]);
  return variants.find((v) => v.metrics.activities.some((a: string) => names.has(a))) ?? null;
}

/** The axis unit for a span: days from two days up, then hours, then minutes. */
export function axisUnit(maxSeconds: number): [number, string] {
  const day = 86400;
  return maxSeconds >= 2 * day ? [day, "d"] : maxSeconds >= 2 * 3600 ? [3600, "h"] : [60, "min"];
}

/** A tick in the axis's own unit, so `0` reads `0 d` on a day scale, never `0 s`. */
export function tickLabel(seconds: number, maxSeconds: number): string {
  const [size, unit] = axisUnit(maxSeconds);
  const value = seconds / size;
  return `${Number.isInteger(value) ? value : value.toFixed(1)} ${unit}`;
}

/** Round axis ticks from 0 to just above `max`, in whole days when the span is days. */
export function ticks(maxSeconds: number, count = 4): number[] {
  if (maxSeconds <= 0) return [0];
  const [unit] = axisUnit(maxSeconds);
  const raw = maxSeconds / unit / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= maxSeconds / unit + step * 0.999; v += step) out.push(v * unit);
  return out;
}
