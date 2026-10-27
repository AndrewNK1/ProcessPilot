// Evidence: what mining found in one segment. The screen answers "where do the
// days go?" first (the headline figures, then one path read as an itinerary),
// then the ranked findings. Every row carries its evidence ID: these are the IDs
// the agent must cite, so any claim in a proposal can be checked against the
// table it came from. Every number here was computed by pandas/pm4py.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ChevronRight } from "lucide-react";
import { api, type EvidenceItem, type Run, type Segment } from "../api";
import { durationParts, fmtDuration, fmtInt, fmtShare } from "../format";
import { readScope, useSettings, writeScope } from "../hooks";
import { bottleneckIds, defaultRank, legs, paths } from "../journey";
import { Itinerary } from "../Itinerary";
import { BpmnView } from "../BpmnView";
import { ApiProblem, Kpis, Notice, Tabs, Tag } from "../ui";

/** Resolve run and segment from the URL, else the remembered scope, else the newest run. */
export function useRunScope(base: "evidence" | "redesign") {
  const { runId, slug } = useParams();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const mined = (runs.data ?? []).filter((r) => r.status === "mined");
  const remembered = readScope();
  const wantedRun = runId ?? (mined.some((r) => r.run_id === remembered.runId) ? remembered.runId : mined[0]?.run_id);
  const run = useQuery({ queryKey: ["run", wantedRun], queryFn: () => api.run(wantedRun as string), enabled: !!wantedRun });
  const segment =
    run.data?.segments.find((s) => s.slug === slug) ??
    (slug ? undefined : run.data?.segments.find((s) => s.slug === remembered.slug) ?? run.data?.segments[0]);
  useEffect(() => {
    if (run.data && segment) writeScope({ runId: run.data.run_id, slug: segment.slug });
  }, [run.data, segment]);
  const redirect =
    run.data && segment && (runId !== run.data.run_id || slug !== segment.slug)
      ? `/${base}/${run.data.run_id}/${segment.slug}`
      : null;
  return { runs, mined, run, segment, redirect, unknownSegment: !!slug && !!run.data && !segment };
}

export function ScopeBar({
  base,
  run,
  segment,
  mined,
}: {
  base: "evidence" | "redesign";
  run: Run;
  segment: Segment;
  mined: { run_id: string; preset: string | null }[];
}) {
  const navigate = useNavigate();
  return (
    <div className="section" style={{ gap: 0 }}>
      <div className="summary-line" style={{ alignItems: "flex-end" }}>
        <div style={{ minWidth: 0, flex: "1 1 420px" }}>
          <Tabs
            label="Segment"
            value={segment.slug}
            onChange={(s) => navigate(`/${base}/${run.run_id}/${s}`)}
            tabs={run.segments.map((s) => ({ id: s.slug, label: s.name }))}
          />
        </div>
        <div className="field" style={{ width: 360, flex: "0 1 360px" }}>
          <label htmlFor="run-pick" className="visually-hidden">
            Mining run
          </label>
          <select id="run-pick" value={run.run_id} onChange={(e) => navigate(`/${base}/${e.target.value}`)}>
            {mined.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                Run {r.run_id} · {r.preset ?? "preset not named"}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}

export function NoRuns() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>No mined run yet</h1>
          <p className="finding">Evidence comes from a mined event log. Start on the Data page.</p>
        </div>
      </div>
      <div className="actions">
        <Link className="btn primary" to="/data">
          Go to Data <ArrowRight size={16} />
        </Link>
      </div>
    </>
  );
}

export function EvidencePage() {
  const { runs, mined, run, segment, redirect, unknownSegment } = useRunScope("evidence");
  if (redirect) return <Navigate to={redirect} replace />;
  if (runs.isError) return <ApiProblem error={runs.error} />;
  if (runs.data && mined.length === 0) return <NoRuns />;
  if (run.isError) return <ApiProblem error={run.error} />;
  if (unknownSegment) return <Notice tone="error">This run has no such segment.</Notice>;
  if (!run.data || !segment) return <p className="muted">Loading the run…</p>;
  return <SegmentEvidence run={run.data} segment={segment} mined={mined} />;
}

function SegmentEvidence({ run, segment, mined }: { run: Run; segment: Segment; mined: { run_id: string; preset: string | null }[] }) {
  const items = useQuery({
    queryKey: ["evidence", run.run_id, segment.slug],
    queryFn: () => api.evidence(run.run_id, segment.slug),
  });
  const byId = useMemo(() => new Map((items.data ?? []).map((i) => [i.id, i])), [items.data]);
  const byType = useMemo(() => {
    const out = new Map<string, EvidenceItem[]>();
    for (const i of items.data ?? []) out.set(i.type, [...(out.get(i.type) ?? []), i]);
    return out;
  }, [items.data]);
  const bottlenecks = (byType.get("bottleneck") ?? []).filter((b) => b.metrics.source !== undefined);
  const worst = bottlenecks[0];
  const m = segment.kpi?.metrics;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">Evidence · run {run.run_id}</div>
          <h1>{segment.name}</h1>
          <p className="finding">
            {worst ? (
              <>
                Cases wait longest between {worst.metrics.source} and {worst.metrics.target}:{" "}
                <b>median {fmtDuration(worst.metrics.median_wait_seconds)}</b> in {fmtShare(worst.metrics.case_share)} of
                cases <Tag id={worst.id} item={worst} />
              </>
            ) : (
              "What mining found in this segment. No bottleneck was ranked."
            )}
          </p>
        </div>
        <Link className="btn primary" to={`/redesign/${run.run_id}/${segment.slug}`}>
          Diagnose and redesign <ArrowRight size={16} />
        </Link>
      </div>

      <ScopeBar base="evidence" run={run} segment={segment} mined={mined} />
      {run.preset === null && <PresetNotice run={run} />}
      {items.isError && <ApiProblem error={items.error} />}

      {m && (
        <Kpis
          cells={[
            { label: "Cases", value: fmtInt(m.case_count) },
            { label: "Events", value: fmtInt(m.event_count) },
            ...(
              [
                ["Median throughput", m.median_throughput_seconds],
                ["Mean throughput", m.mean_throughput_seconds],
                ["P90 throughput", m.p90_throughput_seconds],
              ] as [string, number][]
            ).map(([label, s]) => {
              const [value, unit] = durationParts(s);
              return { label, value, unit };
            }),
          ]}
        />
      )}

      {items.data && (
        <div className="grid-2">
          <PathSection run={run} segment={segment} items={items.data} byId={byId} />
          <WorstLayovers bottlenecks={bottlenecks.slice(0, 6)} />
        </div>
      )}

      {items.data && <AllFindings run={run} segment={segment} byType={byType} items={items.data} />}
      {run.data_quality && <RunQuality run={run} />}
    </>
  );
}

function PathSection({
  run,
  segment,
  items,
  byId,
}: {
  run: Run;
  segment: Segment;
  items: EvidenceItem[];
  byId: Map<string, EvidenceItem>;
}) {
  const [params, setParams] = useSearchParams();
  const variants = paths(items);
  const ranks = variants.map((v) => v.metrics.rank as number);
  const asked = Number(params.get("path"));
  const rank = ranks.includes(asked) ? asked : defaultRank(variants);
  const waits = useQuery({
    queryKey: ["waits", run.run_id, segment.slug, rank],
    queryFn: () => api.pathWaits(run.run_id, segment.slug, rank),
    enabled: variants.length > 0,
    staleTime: Infinity,
  });
  const bn = useMemo(() => bottleneckIds(items), [items]);
  const variant = variants.find((v) => v.metrics.rank === rank);
  if (!variants.length) return <p className="empty">No path was listed for this segment.</p>;
  return (
    <section className="coupon">
      <header className="coupon-head">
        <span className="caps">Itinerary · path {rank}</span>
        {variant && (
          <span className="head-meta num">
            {fmtShare(variant.metrics.case_share)} of cases · {variant.metrics.activities.length} steps
          </span>
        )}
      </header>
      <div className="coupon-body">
        <div className="summary-line">
          <p className="small muted" style={{ maxWidth: "60ch" }}>
            The cases that followed exactly this path, read as a journey: each layover is the mean wait before a step,
            so the layovers add up to the path's mean throughput. A wait includes the step's own work.
          </p>
          <div className="field" style={{ width: 240 }}>
            <label htmlFor="path-pick" className="visually-hidden">
              Path
            </label>
            <select
              id="path-pick"
              value={rank}
              onChange={(e) => setParams({ path: e.target.value }, { replace: true })}
            >
              {variants.map((v) => (
                <option key={v.id} value={v.metrics.rank}>
                  Path {v.metrics.rank} · {fmtShare(v.metrics.case_share)} · {v.metrics.activities.length} steps
                </option>
              ))}
            </select>
          </div>
        </div>
        {waits.isError && <ApiProblem error={waits.error} />}
        {waits.data && <Itinerary waits={waits.data} legs={legs(waits.data, bn)} evidence={byId} />}
        {variant && (
          <p className="small muted">
            Path evidence <Tag id={variant.id} item={variant} /> · median throughput{" "}
            {fmtDuration(variant.metrics.median_throughput_seconds)}
          </p>
        )}
      </div>
    </section>
  );
}

function WorstLayovers({ bottlenecks }: { bottlenecks: EvidenceItem[] }) {
  return (
    <section className="coupon">
      <header className="coupon-head quiet">
        <span className="caps">Longest layovers</span>
        <span className="head-meta">by impact</span>
      </header>
      <div className="coupon-body" style={{ gap: 0, paddingTop: 6 }}>
        {bottlenecks.length === 0 && <p className="small muted">No bottleneck was ranked.</p>}
        {bottlenecks.map((b, n) => (
          <div
            key={b.id}
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0,1fr) auto",
              gap: "4px 12px",
              padding: "12px 0",
              borderTop: n ? "1px solid var(--rule)" : 0,
            }}
          >
            <span style={{ minWidth: 0 }}>
              {b.metrics.source} <span className="muted">→</span> {b.metrics.target}
            </span>
            <b className="num" style={{ color: "var(--carbon)", textAlign: "right" }}>
              {fmtDuration(b.metrics.median_wait_seconds)}
            </b>
            <span className="small muted num" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Tag id={b.id} item={b} /> {fmtShare(b.metrics.case_share)} of cases
            </span>
            <span className="small muted" style={{ textAlign: "right" }}>
              median wait
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

// --- the ranked tables ----------------------------------------------------------------

type Col = { head: string; right?: boolean; wide?: boolean; cell: (m: Record<string, any>) => ReactNode };

const share: Col = {
  head: "Cases",
  right: true,
  cell: (m) => (
    <span className="share">
      {fmtShare(m.case_share)}
      <i style={{ ["--w" as string]: `${Math.min(100, m.case_share * 100)}%` }} />
    </span>
  ),
};
const dur = (head: string, key: string): Col => ({ head, right: true, cell: (m) => fmtDuration(m[key]) });

const COLUMNS: Record<string, Col[]> = {
  bottleneck: [
    { head: "Transition", wide: true, cell: (m) => <>{m.source} <span className="muted">→</span> {m.target}</> },
    share,
    dur("Median wait", "median_wait_seconds"),
    dur("P90 wait", "p90_wait_seconds"),
    { head: "Occurrences", right: true, cell: (m) => fmtInt(m.frequency) },
  ],
  activity: [
    { head: "Activity", wide: true, cell: (m) => m.activity },
    share,
    dur("Median wait before", "median_wait_seconds"),
    dur("P90 wait before", "p90_wait_seconds"),
  ],
  variant: [
    { head: "Path", wide: true, cell: (m) => m.activities.join(" → ") },
    share,
    dur("Median throughput", "median_throughput_seconds"),
  ],
  rework: [
    { head: "Activity", wide: true, cell: (m) => m.activity },
    share,
    { head: "Extra executions", right: true, cell: (m) => fmtInt(m.extra_occurrences) },
    dur("Extra median time", "extra_median_throughput_seconds"),
  ],
  conformance: [
    { head: "Rule", wide: true, cell: (m) => m.description ?? m.rule_name },
    { head: "Violations, all cases", right: true, cell: (m) => fmtShare(m.violation_share) },
    { head: "Completed cases", right: true, cell: (m) => fmtShare(m.completed_violation_share) },
  ],
};

const TYPES: { id: string; label: string }[] = [
  { id: "bottleneck", label: "Bottlenecks" },
  { id: "activity", label: "Waits before steps" },
  { id: "variant", label: "Paths" },
  { id: "rework", label: "Rework" },
  { id: "resource", label: "Resources and handovers" },
  { id: "conformance", label: "Conformance" },
  { id: "model", label: "As-is model" },
  { id: "all", label: "All items" },
];

function tabulates(type: string, item: EvidenceItem): boolean {
  const cols = COLUMNS[type];
  if (!cols || item.metrics.available === false) return false;
  try {
    cols.forEach((c) => c.cell(item.metrics));
    return Object.keys(item.metrics).length > 0 && !("available" in item.metrics && !item.metrics.available);
  } catch {
    return false; // a notice item (e.g. "not performed") has no metrics to tabulate
  }
}

function EvidenceTable({ type, items }: { type: string; items: EvidenceItem[] }) {
  const cols = COLUMNS[type] ?? [];
  const rows = items.map((i) => ({ item: i, table: tabulates(type, i) }));
  const anyTable = rows.some((r) => r.table);
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th className="id-col">ID</th>
            {anyTable
              ? cols.map((c) => (
                  <th key={c.head} className={`${c.right ? "r" : ""}${c.wide ? " wide" : ""}`}>
                    {c.head}
                  </th>
                ))
              : <th className="wide">Summary (written by code)</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ item, table }) => (
            <tr key={item.id}>
              <td className="id-col">
                <Tag id={item.id} item={item} />
              </td>
              {table
                ? cols.map((c) => (
                    <td key={c.head} className={c.right ? "r" : undefined}>
                      {c.cell(item.metrics)}
                    </td>
                  ))
                : <td colSpan={anyTable ? cols.length : 1}>{item.summary}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AllFindings({
  run,
  segment,
  byType,
  items,
}: {
  run: Run;
  segment: Segment;
  byType: Map<string, EvidenceItem[]>;
  items: EvidenceItem[];
}) {
  const [tab, setTab] = useState("bottleneck");
  const settings = useSettings();
  const xml = useQuery({
    queryKey: ["as-is", run.run_id, segment.slug],
    queryFn: () => api.asIsBpmn(run.run_id, segment.slug),
    enabled: tab === "model" && segment.has_model,
    staleTime: Infinity,
  });
  const selected = tab === "all" ? items : byType.get(tab) ?? [];
  return (
    <section className="section">
      <div className="section-head">
        <div>
          <h2>All findings</h2>
          <p>Ranked by impact (median wait × share of cases). Each ID is what the agent must cite.</p>
        </div>
      </div>
      <Tabs
        label="Finding type"
        value={tab}
        onChange={setTab}
        tabs={TYPES.map((t) => ({
          ...t,
          count: t.id === "model" ? undefined : t.id === "all" ? items.length : byType.get(t.id)?.length ?? 0,
        }))}
      />
      {tab === "model" ? (
        segment.has_model ? (
          <>
            <p className="small muted">
              Discovered with the inductive miner. Rare activities may be filtered out of the model; they still appear in
              the tables. Scroll to zoom, drag to pan.
            </p>
            {xml.isError ? (
              <ApiProblem error={xml.error} />
            ) : (
              <BpmnView xml={xml.data} height={settings.bpmn_height_px} label={`As-is model of ${segment.name}`} />
            )}
          </>
        ) : (
          <p className="empty">This run has no as-is model for the segment (mined before Phase 4).</p>
        )
      ) : selected.length ? (
        <EvidenceTable type={tab === "all" ? "all" : tab} items={selected} />
      ) : (
        <p className="empty">No items of this type for this segment.</p>
      )}
    </section>
  );
}

function RunQuality({ run }: { run: Run }) {
  const q = run.data_quality!;
  const removed = q.cleaning_log.length;
  return (
    <details className="disclosure">
      <summary>
        <ChevronRight size={16} /> Data quality and cleaning of this run
        <span>
          {q.issue_count} findings reported · {removed ? `${removed} cleaning steps logged` : "nothing removed"}
        </span>
      </summary>
      <div className="disclosure-body">
        {removed ? (
          <div className="table-wrap">
            <table className="data small">
              <thead>
                <tr>
                  {Object.keys(q.cleaning_log[0]).map((k) => (
                    <th key={k}>{k}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.cleaning_log.map((row, n) => (
                  <tr key={n}>
                    {Object.values(row).map((v, m) => (
                      <td key={m}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="small">Nothing was removed before mining.</p>
        )}
      </div>
    </details>
  );
}

function PresetNotice({ run }: { run: Run }) {
  const client = useQueryClient();
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets });
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: (preset: string) => api.setRunPreset(run.run_id, preset),
    onSuccess: () => client.invalidateQueries(),
  });
  const chosen = name || presets.data?.[0]?.name || "";
  return (
    <div className="notice wait">
      <div className="section" style={{ gap: 10, width: "100%" }}>
        <span>
          <b>Name the preset this run was mined with.</b> It was found on disk (for example mined in a notebook); the
          agent needs the preset's constraints and domain knowledge before it can redesign.
        </span>
        <form
          className="actions"
          onSubmit={(e) => {
            e.preventDefault();
            if (chosen) save.mutate(chosen);
          }}
        >
          <label htmlFor="run-preset" className="visually-hidden">
            Preset
          </label>
          <select id="run-preset" value={chosen} onChange={(e) => setName(e.target.value)} style={{ width: 220 }}>
            {(presets.data ?? []).map((p) => (
              <option key={p.name}>{p.name}</option>
            ))}
          </select>
          <button className="btn" type="submit" disabled={!chosen || save.isPending}>
            Save preset
          </button>
        </form>
        {save.isError && <ApiProblem error={save.error} />}
      </div>
    </div>
  );
}
