import { useRef } from "react";
import type { EvidenceItem, PathWaits } from "./api";
import { fmtDuration, fmtInt } from "./format";
import { type Leg, tickLabel, ticks } from "./journey";
import { useItineraryDraw } from "./motion";
import { Tag } from "./ui";

const MARK_TEXT: Record<string, string> = {
  automated: "automated",
  removed: "removed",
  moved: "moved",
  parallel: "in parallel",
  merged: "merged",
  added: "new step",
};

/**
 * One path as an itinerary: departure, arrival and total on top, one strip with
 * every layover end to end, then each leg with its layover on a shared scale.
 */
export function Itinerary({
  waits,
  legs,
  evidence,
  compact,
}: {
  waits: PathWaits;
  legs: Leg[];
  evidence: Map<string, EvidenceItem>;
  compact?: boolean;
}) {
  const timed = legs.filter((l) => l.meanSeconds !== null);
  const total = timed.reduce((sum, l) => sum + (l.meanSeconds ?? 0), 0);
  const longest = Math.max(0, ...timed.map((l) => l.meanSeconds ?? 0));
  const axis = ticks(longest);
  const scale = axis[axis.length - 1] || 1;
  const first = waits.activities[0];
  const last = waits.activities[waits.activities.length - 1];
  const root = useRef<HTMLDivElement>(null);
  useItineraryDraw(root, `${waits.evidence_id}:${legs.map((l) => `${l.activity}/${l.mark ?? ""}`).join("|")}`);

  return (
    <div className="section" style={{ gap: 16 }} ref={root}>
      {!compact && (
        <>
          <div className="route">
            <div className="stop">
              Departs at
              <strong>{first}</strong>
            </div>
            <div className="total">
              <b className="num">{fmtDuration(waits.mean_throughput_seconds)}</b>
              mean, {fmtInt(waits.case_count)} {waits.case_count === 1 ? "case" : "cases"}
            </div>
            <div className="stop end">
              Arrives at
              <strong>{last}</strong>
            </div>
          </div>
          <div className="strip" aria-hidden>
            {timed.map((l, i) => (
              <span
                key={`${l.activity}-${i}`}
                className={l.meanSeconds === longest ? "worst" : undefined}
                style={{ flex: Math.max(l.meanSeconds ?? 0, total / 400) }}
                title={`${l.activity}: ${fmtDuration(l.meanSeconds)} layover`}
              />
            ))}
          </div>
        </>
      )}
      <div>
        <div className="legs" role="table" aria-label={`Layovers on ${waits.evidence_id}`}>
          {legs.map((leg, i) => {
            const start = leg.meanSeconds === null && leg.mark !== "added";
            const worst = leg.meanSeconds !== null && leg.meanSeconds === longest;
            return (
              <div
                key={`${leg.activity}-${i}`}
                role="row"
                className={`leg-row${start ? " start" : ""}${leg.mark ? ` mark-${leg.mark}` : ""}`}
              >
                <div className="name" role="cell">
                  <span>{leg.activity}</span>
                  {(leg.bottleneckId || leg.mark) && (
                    <small>
                      {leg.bottleneckId && <Tag id={leg.bottleneckId} item={evidence.get(leg.bottleneckId)} />}
                      {leg.mark && <span>{MARK_TEXT[leg.mark]}</span>}
                    </small>
                  )}
                </div>
                <div className="track" role="cell" aria-hidden={start || leg.mark === "added"}>
                  {leg.meanSeconds !== null && (
                    <i
                      className={worst ? "worst" : undefined}
                      style={{ ["--w" as string]: `${(leg.meanSeconds / scale) * 100}%` }}
                      title={`mean ${fmtDuration(leg.meanSeconds)}, median ${fmtDuration(leg.medianSeconds)}, P90 ${fmtDuration(leg.p90Seconds)} since the previous step`}
                    />
                  )}
                </div>
                <div className="val" role="cell">
                  {start ? (
                    "departs"
                  ) : leg.mark === "added" ? (
                    <small>wait assumed</small>
                  ) : (
                    <>
                      <b>{fmtDuration(leg.meanSeconds)}</b>
                      <small>median {fmtDuration(leg.medianSeconds)}</small>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="axis num" aria-hidden>
          <div />
          <div>
            {axis.map((t) => (
              <span key={t} style={{ left: `${(t / scale) * 100}%` }}>
                {tickLabel(t, scale)}
              </span>
            ))}
          </div>
          <div />
        </div>
      </div>
    </div>
  );
}
