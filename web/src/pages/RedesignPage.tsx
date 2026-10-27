// Redesign: run the agent on a segment, read its diagnosis and proposals, decide.
//
// What the analyst needs to judge a proposal sits on its coupon: the evidence
// it cites (with the code-written summaries), the exact edit operations, the
// soundness verdict, the replay ESTIMATE torn off as a carbon copy of today,
// and the legs of the itinerary it acts on. The decision goes to the API, which
// logs it and resumes the paused LangGraph run; ProcessPilot never executes a
// change. Earlier rounds stay on file, voided, never deleted.

import { useMemo, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight } from "lucide-react";
import {
  api,
  type AgentRun,
  type EvidenceItem,
  type ProposalView,
  type Run,
  type Segment,
} from "../api";
import { fmtDuration, fmtInt, fmtShare, fmtStamp } from "../format";
import { usePersisted, useJob, useSettings } from "../hooks";
import { bottleneckIds, legs, pathForProposal, paths, touched } from "../journey";
import { Itinerary } from "../Itinerary";
import { useEstimateDraw, useStampLanding } from "../motion";
import { BpmnView } from "../BpmnView";
import { ApiProblem, Coupon, JobLine, Notice, Stamp, StatusStamp, Tabs, Tag } from "../ui";
import { NoRuns, ScopeBar, useRunScope } from "./EvidencePage";

const AUTOMATED_SUFFIX = " (automated)"; // must match processpilot.process_tree.AUTOMATED_SUFFIX

export function RedesignPage() {
  const { runs, mined, run, segment, redirect, unknownSegment } = useRunScope("redesign");
  if (redirect) return <Navigate to={redirect} replace />;
  if (runs.isError) return <ApiProblem error={runs.error} />;
  if (runs.data && mined.length === 0) return <NoRuns />;
  if (run.isError) return <ApiProblem error={run.error} />;
  if (unknownSegment) return <Notice tone="error">This run has no such segment.</Notice>;
  if (!run.data || !segment) return <p className="muted">Loading the run…</p>;
  return <SegmentRedesign run={run.data} segment={segment} mined={mined} />;
}

function SegmentRedesign({ run, segment, mined }: { run: Run; segment: Segment; mined: { run_id: string; preset: string | null }[] }) {
  const [params, setParams] = useSearchParams();
  const agentRuns = useQuery({
    queryKey: ["agent-runs", run.run_id, segment.slug],
    queryFn: () => api.agentRuns(run.run_id, segment.slug),
  });
  const list = agentRuns.data ?? [];
  const asked = params.get("agent");
  const agentRunId = list.some((a) => a.agent_run_id === asked) ? asked : list[0]?.agent_run_id ?? null;
  const settings = useSettings();
  const detail = useQuery({
    queryKey: ["agent-run", run.run_id, segment.slug, agentRunId],
    queryFn: () => api.agentRun(run.run_id, segment.slug, agentRunId as string),
    enabled: !!agentRunId,
    refetchInterval: (q) => (q.state.data?.busy || q.state.data?.status === "running" ? settings.poll_seconds * 1000 : false),
  });
  const evidence = useQuery({
    queryKey: ["evidence", run.run_id, segment.slug],
    queryFn: () => api.evidence(run.run_id, segment.slug),
  });
  const byId = useMemo(() => new Map((evidence.data ?? []).map((i) => [i.id, i])), [evidence.data]);
  const d = detail.data;

  if (run.preset === null) {
    return (
      <>
        <Head run={run} segment={segment} detail={undefined} />
        <Notice tone="wait">
          <b>Name the preset this run was mined with first.</b> The agent needs its constraints; do it on the Evidence
          page.
        </Notice>
        <div className="actions">
          <Link className="btn" to={`/evidence/${run.run_id}/${segment.slug}`}>
            Go to Evidence <ArrowRight size={16} />
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      <Head run={run} segment={segment} detail={d} />
      <ScopeBar base="redesign" run={run} segment={segment} mined={mined} />
      {agentRuns.isError && <ApiProblem error={agentRuns.error} />}

      <StartRun
        run={run}
        segment={segment}
        open={agentRuns.isSuccess && list.length === 0}
        onStarted={(id) => setParams({ agent: id })}
      />

      {list.length > 1 && (
        <div className="field" style={{ maxWidth: 520 }}>
          <label htmlFor="agent-pick">Agent run</label>
          <select id="agent-pick" value={agentRunId ?? ""} onChange={(e) => setParams({ agent: e.target.value })}>
            {list.map((a) => (
              <option key={a.agent_run_id} value={a.agent_run_id}>
                {a.agent_run_id} · {a.status.replace("_", " ")} · model {a.model_id}
              </option>
            ))}
          </select>
        </div>
      )}

      {detail.isError && <ApiProblem error={detail.error} />}
      {d && <AgentRunView run={run} segment={segment} detail={d} evidence={byId} items={evidence.data ?? []} />}
    </>
  );
}

function Head({ run, segment, detail }: { run: Run; segment: Segment; detail: AgentRun | undefined }) {
  const stamp = useRef<HTMLSpanElement>(null);
  useStampLanding(stamp, detail?.agent_run_id ?? "", detail?.status ?? "");
  let finding = "The agent reads the mined evidence and proposes redesigns from a fixed heuristic catalogue. You approve or reject; nothing is ever executed.";
  if (detail?.awaiting_review) {
    finding = `${detail.proposals.length} proposals wait for your decision, round ${detail.review_round}. Nothing is executed whatever you decide.`;
  } else if (detail) {
    finding = `This agent run is ${detail.status.replace("_", " ")}.`;
  }
  return (
    <div className="page-head">
      <div>
        <div className="crumb">Redesign · run {run.run_id}</div>
        <h1>{segment.name}</h1>
        <p className="finding">{finding}</p>
      </div>
      {detail && (
        <div className="actions">
          <span className="landing" ref={stamp}>
            <StatusStamp status={detail.status} tilt />
          </span>
          {detail.awaiting_review && (
            <a className="btn primary" href="#decision">
              Go to the decision <ArrowRight size={16} />
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// --- starting a run ---------------------------------------------------------------------

function StartRun({
  run,
  segment,
  open,
  onStarted,
}: {
  run: Run;
  segment: Segment;
  open: boolean;
  onStarted: (agentRunId: string) => void;
}) {
  const model = useQuery({ queryKey: ["model", run.run_id, segment.slug], queryFn: () => api.model(run.run_id, segment.slug) });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [protectedSet, setProtected] = useState<Set<string> | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [modelId, setModelId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId, (j) => j.status === "succeeded" && j.result?.agent_run_id && onStarted(j.result.agent_run_id));
  const defaults = model.data?.default_constraints ?? { text: [], protected_activities: [] };
  const activities = model.data?.activities ?? [];
  const chosen = protectedSet ?? new Set(defaults.protected_activities.filter((a) => activities.includes(a)));
  const constraintsText = text ?? defaults.text.join("\n");
  const modelValue = modelId ?? health.data?.model_id ?? "";
  const start = useMutation({
    mutationFn: () =>
      api.startAgent(
        run.run_id,
        segment.slug,
        {
          text: constraintsText.split("\n").map((l) => l.trim()).filter(Boolean),
          protected_activities: [...chosen],
        },
        modelValue.trim() || null,
      ),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const working = !!jobId && job?.status !== "failed" && job?.status !== "succeeded";

  return (
    <details className="disclosure" open={open || working || undefined}>
      <summary>
        <ChevronRight size={16} /> Start a new agent run
        <span>reads this segment's evidence, proposes 2–3 redesigns</span>
      </summary>
      <form
        className="disclosure-body"
        onSubmit={(e) => {
          e.preventDefault();
          start.mutate();
        }}
      >
        {model.isError && <ApiProblem error={model.error} />}
        <div className="field">
          <span className="label">Protected activities</span>
          <div className="chips" role="group" aria-label="Protected activities">
            {activities.map((a) => (
              <label key={a} className="chip">
                <input
                  type="checkbox"
                  checked={chosen.has(a)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(a);
                    else next.delete(a);
                    setProtected(next);
                  }}
                />
                {a}
              </label>
            ))}
          </div>
          <span className="hint">Never removed, moved, merged or parallelised. They may still be automated.</span>
          {model.data && model.data.unmodelled_activities.length > 0 && (
            <span className="hint">
              Too rare to be in the as-is model (can be discussed, not edited): {model.data.unmodelled_activities.join(", ")}.
            </span>
          )}
        </div>
        <div className="field">
          <label htmlFor="constraints">Constraints for the agent, one per line</label>
          <textarea id="constraints" value={constraintsText} onChange={(e) => setText(e.target.value)} rows={4} />
        </div>
        <div className="field" style={{ maxWidth: 420 }}>
          <label htmlFor="model-id">Model</label>
          <input id="model-id" type="text" value={modelValue} onChange={(e) => setModelId(e.target.value)} aria-describedby="model-hint" />
          <span className="hint" id="model-hint">
            mock is the rule-based baseline (no API key). Any id init_chat_model accepts works, e.g.
            anthropic:claude-opus-5-5.
          </span>
        </div>
        {start.isError && <ApiProblem error={start.error} />}
        <JobLine job={job} label="Agent run" />
        <div className="actions">
          <button className="btn primary" type="submit" disabled={start.isPending || working}>
            Run the agent
          </button>
        </div>
      </form>
    </details>
  );
}

// --- one agent run ----------------------------------------------------------------------

function AgentRunView({
  run,
  segment,
  detail,
  evidence,
  items,
}: {
  run: Run;
  segment: Segment;
  detail: AgentRun;
  evidence: Map<string, EvidenceItem>;
  items: EvidenceItem[];
}) {
  const state = detail.state;
  const [tab, setTab] = useState("0");
  const proposals = detail.proposals;
  const current = proposals[Number(tab)] ?? proposals[0];
  const scales = useMemo(() => estimateScales(proposals), [proposals]);

  return (
    <>
      <p className="small muted num">
        Agent run {detail.agent_run_id} · model {detail.model_id} · started {fmtStamp(detail.created_at)} UTC
        {detail.model_id === "mock" && " · rule-based mock: no language model was involved, every text starts with [mock]"}
      </p>
      {detail.busy && (
        <div className="progress" role="status">
          <span className="spinner" aria-hidden /> A background job is still working on this run.
        </div>
      )}
      {!detail.busy && state === null && <Notice tone="wait">No checkpoint for this run yet.</Notice>}
      {state?.error && <Notice tone="error">{state.error}</Notice>}
      {state && state.warnings.length > 0 && (
        <details className="disclosure">
          <summary>
            <ChevronRight size={16} /> Validation warnings kept after retries <span>{state.warnings.length}</span>
          </summary>
          <div className="disclosure-body small">
            {state.warnings.map((w, n) => (
              <p key={n}>
                <b>{w.code}</b> {w.path}: {w.message}
              </p>
            ))}
          </div>
        </details>
      )}

      {state?.diagnosis && (
        <Coupon label="Diagnosis" meta={`${state.diagnosis.hypotheses.length} hypotheses · each cites its evidence`}>
          <div>
            {state.diagnosis.hypotheses.map((h, n) => (
              <div className="claim" key={n}>
                <div className="section" style={{ gap: 8 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <h3>Hypothesis {n + 1}</h3>
                    <span className={`pill ${h.confidence === "high" ? "go" : h.confidence === "medium" ? "wait" : ""}`}>
                      {h.confidence} confidence
                    </span>
                  </div>
                  <p>{h.description}</p>
                  {h.alternative_explanations.length > 0 && (
                    <p className="small muted">Could also be: {h.alternative_explanations.join(" · ")}</p>
                  )}
                </div>
                <Citations ids={h.evidence_ids} evidence={evidence} />
              </div>
            ))}
          </div>
        </Coupon>
      )}

      {proposals.length > 0 && current && (
        <section className="section">
          <div className="section-head">
            <div>
              <h2>Proposals · revision {state?.revision_count ?? 0}</h2>
              <p>One coupon per proposal. The decision below applies to all of them.</p>
            </div>
          </div>
          <Tabs
            label="Proposal"
            value={String(current.index)}
            onChange={setTab}
            tabs={proposals.map((p) => ({ id: String(p.index), label: `P${p.index + 1} · ${short(p.proposal.title)}` }))}
          />
          <ProposalCoupon run={run} segment={segment} view={current} evidence={evidence} items={items} scales={scales} />
        </section>
      )}

      {proposals.some((p) => p.edit?.status === "applied") && (
        <CompareModels run={run} segment={segment} detail={detail} index={current?.index ?? 0} />
      )}

      {/* Keyed by round: each round gets an empty form, never the previous round's choice. */}
      {detail.awaiting_review && (
        <DecisionForm key={`${detail.agent_run_id}-${detail.review_round}`} run={run} segment={segment} detail={detail} />
      )}
      <DecisionLog detail={detail} />
    </>
  );
}

const short = (t: string, limit = 40) => (t.length <= limit ? t : `${t.slice(0, limit - 1).trimEnd()}…`);

/** `H-TASK-ELIMINATION` -> `Task elimination · H-TASK-ELIMINATION` (the id stays citable). */
function heuristicName(id: string): string {
  const words = id.replace(/^H-/, "").replace(/-/g, " ").toLowerCase();
  return `${words.charAt(0).toUpperCase()}${words.slice(1)} · ${id}`;
}

function Citations({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceItem> }) {
  return (
    <div className="cites">
      <span>Cites {ids.length} evidence {ids.length === 1 ? "item" : "items"}</span>
      {ids.map((id) => {
        const item = evidence.get(id);
        return (
          <div key={id}>
            <Tag id={id} item={item} missing={!item} />
            <span>{item ? item.summary : "Not in this segment's evidence."}</span>
          </div>
        );
      })}
    </div>
  );
}

// One scale per metric, shared by every proposal, so coupons compare without re-reading axes.
function estimateScales(proposals: ProposalView[]): { median: number; p90: number } {
  let median = 0;
  let p90 = 0;
  for (const p of proposals) {
    const e = p.validation?.estimate;
    if (!e) continue;
    median = Math.max(median, e.as_is.median_seconds, e.to_be.median_seconds);
    p90 = Math.max(p90, e.as_is.p90_seconds, e.to_be.p90_seconds);
  }
  return { median: median || 1, p90: p90 || 1 };
}

function ProposalCoupon({
  run,
  segment,
  view,
  evidence,
  items,
  scales,
}: {
  run: Run;
  segment: Segment;
  view: ProposalView;
  evidence: Map<string, EvidenceItem>;
  items: EvidenceItem[];
  scales: { median: number; p90: number };
}) {
  const p = view.proposal;
  const sound = view.validation?.soundness?.sound;
  return (
    <Coupon
      no={`P${view.index + 1}`}
      label={heuristicName(p.heuristic_id)}
      meta={
        sound === undefined ? "Not validated" : sound ? "Sound model" : "Not sound"
      }
      stub={<EstimateStub view={view} scales={scales} />}
    >
      <h2 style={{ fontSize: 19, lineHeight: "26px" }}>{p.title}</h2>
      {view.edit && view.edit.status !== "applied" && (
        <Notice tone="error">Edits could not be applied: {view.edit.message}</Notice>
      )}
      {sound === false && view.validation?.soundness && (
        <Notice tone="error">The to-be model is not sound: {view.validation.soundness.method}</Notice>
      )}
      <div className="split">
        <div className="section" style={{ gap: 14 }}>
          <div>
            <h3>Changes</h3>
            <ul className="bullets" style={{ marginTop: 6 }}>
              {view.operations_text.map((op) => (
                <li key={op}>{op}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Rationale</h3>
            <p style={{ marginTop: 4 }}>{p.rationale}</p>
          </div>
          <div>
            <h3>Expected effect</h3>
            <p style={{ marginTop: 4 }}>{p.expected_effect}</p>
          </div>
        </div>
        <Citations ids={p.evidence_ids} evidence={evidence} />
      </div>
      <WhereItActs run={run} segment={segment} view={view} evidence={evidence} items={items} />
      <div className="split">
        <div>
          <h3>Risks</h3>
          <ul className="bullets" style={{ marginTop: 6 }}>
            {p.risks.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Assumptions</h3>
          <ul className="bullets" style={{ marginTop: 6 }}>
            {p.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      </div>
    </Coupon>
  );
}

function WhereItActs({
  run,
  segment,
  view,
  evidence,
  items,
}: {
  run: Run;
  segment: Segment;
  view: ProposalView;
  evidence: Map<string, EvidenceItem>;
  items: EvidenceItem[];
}) {
  const path = pathForProposal(paths(items), view.proposal);
  const rank = path?.metrics.rank as number | undefined;
  const waits = useQuery({
    queryKey: ["waits", run.run_id, segment.slug, rank],
    queryFn: () => api.pathWaits(run.run_id, segment.slug, rank as number),
    enabled: rank !== undefined,
    staleTime: Infinity,
  });
  const bn = useMemo(() => bottleneckIds(items), [items]);
  if (!path || !waits.data) return null;
  const { marks, added } = touched(view.proposal);
  return (
    <div className="section" style={{ gap: 10 }}>
      <div>
        <h3>Where it acts</h3>
        <p className="small muted" style={{ marginTop: 2 }}>
          Path {rank} <Tag id={path.id} item={path} />, the most frequent listed path this proposal touches. Bars are
          today's layovers; the marks show what would change.
        </p>
      </div>
      <Itinerary waits={waits.data} legs={legs(waits.data, bn, marks, added)} evidence={evidence} compact />
    </div>
  );
}

function EstimateStub({ view, scales }: { view: ProposalView; scales: { median: number; p90: number } }) {
  const v = view.validation;
  const e = v?.estimate;
  const root = useRef<HTMLDivElement>(null);
  useEstimateDraw(root, e ? `${view.index}:${e.as_is.median_seconds}:${e.to_be.median_seconds}:${e.to_be.p90_seconds}` : "");
  if (!v) return <p className="small muted">ESTIMATE · not validated.</p>;
  if (!e) return <p className="small muted">ESTIMATE · {v.message ?? "no estimate for this proposal."}</p>;
  const change = e.median_change_seconds;
  const delta =
    change === null || e.median_change_share === null
      ? null
      : change === 0
        ? { text: "no change", tone: "" }
        : {
            text: `${change < 0 ? "−" : "+"}${fmtShare(Math.abs(e.median_change_share))} (${change < 0 ? "−" : "+"}${fmtDuration(Math.abs(change))})`,
            tone: change < 0 ? "good" : "bad",
          };
  const rows: [string, number, number, number][] = [
    ["Median", e.as_is.median_seconds, e.to_be.median_seconds, scales.median],
    ["P90", e.as_is.p90_seconds, e.to_be.p90_seconds, scales.p90],
  ];
  return (
    <div className="estimate" ref={root}>
      <div className="summary-line">
        <span>
          <span className="caps" style={{ color: "var(--carbon)" }}>
            Estimate
          </span>
          <span className="small muted"> · replay of observed waits, not a simulation</span>
        </span>
        {delta && (
          <span className="small">
            Median throughput <span className={`delta ${delta.tone}`}>{delta.text}</span>
          </span>
        )}
      </div>
      {rows.map(([label, now, est, scale]) => (
        <div key={label} className="section" style={{ gap: 4 }}>
          <div className="estimate-row">
            <span className="muted">{label} today</span>
            <span className="bar" style={{ ["--w" as string]: `${(now / scale) * 100}%` }} />
            <span className="v">{fmtDuration(now)}</span>
          </div>
          <div className="estimate-row">
            <span style={{ color: "var(--carbon)", fontWeight: 600 }}>ESTIMATE</span>
            <span
              className="bar est"
              style={{ ["--w" as string]: `${(est / scale) * 100}%` }}
              data-from={`${(now / scale) * 100}%`}
              data-to={`${(est / scale) * 100}%`}
            />
            <span className="v" style={{ color: "var(--carbon)", fontWeight: 600 }}>
              {fmtDuration(est)}
            </span>
          </div>
        </div>
      ))}
      <p className="small muted num">
        Touches {fmtInt(e.cases_affected)} of {fmtInt(e.case_count)} cases ({fmtShare(e.cases_affected_share)}).
      </p>
      {(e.assumptions.length > 0 || e.not_estimated.length > 0) && (
        <details>
          <summary className="small" style={{ cursor: "pointer", fontWeight: 600 }}>
            Assumptions and limits
          </summary>
          <ul className="bullets small muted" style={{ marginTop: 8 }}>
            {e.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
            {e.not_estimated.map((n) => (
              <li key={n}>Not estimated: {n}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function CompareModels({ run, segment, detail, index }: { run: Run; segment: Segment; detail: AgentRun; index: number }) {
  const settings = useSettings();
  const applied = detail.proposals.filter((p) => p.edit?.status === "applied");
  const [chosen, setChosen] = useState<number | null>(null);
  const shown = applied.find((p) => p.index === (chosen ?? index)) ?? applied[0];
  const asIs = useQuery({
    queryKey: ["as-is", run.run_id, segment.slug],
    queryFn: () => api.asIsBpmn(run.run_id, segment.slug),
    staleTime: Infinity,
  });
  const toBe = useQuery({
    queryKey: ["to-be", run.run_id, segment.slug, detail.agent_run_id, shown.index],
    queryFn: () => api.toBeBpmn(run.run_id, segment.slug, detail.agent_run_id, shown.index),
    staleTime: Infinity,
  });
  const diff = shown.edit?.diff;
  return (
    <section className="section">
      <div className="section-head">
        <div>
          <h2>As-is and to-be model</h2>
          <p>
            {diff?.changes.length ? `Outlined in both models: ${diff.changes.join("; ")}. ` : ""}Scroll to zoom, drag to
            pan.
          </p>
        </div>
        <div className="field" style={{ width: 320 }}>
          <label htmlFor="compare-pick" className="visually-hidden">
            Proposal
          </label>
          <select id="compare-pick" value={shown.index} onChange={(e) => setChosen(Number(e.target.value))}>
            {applied.map((p) => (
              <option key={p.index} value={p.index}>
                P{p.index + 1} · {p.proposal.title}
              </option>
            ))}
          </select>
        </div>
      </div>
      {/* Stacked, not side by side: discovered models are wide and flat, and a half-width
          frame shrinks their labels below reading size. */}
      <div>
        <div className="bpmn-label">As-is</div>
        <BpmnView
          xml={asIs.data}
          height={settings.bpmn_height_px}
          label={`As-is model of ${segment.name}`}
          highlight={[...(diff?.removed ?? []), ...(diff?.automated ?? [])]}
        />
      </div>
      <div>
        <div className="bpmn-label">To-be, proposal {shown.index + 1}</div>
        <BpmnView
          xml={toBe.data}
          height={settings.bpmn_height_px}
          label={`To-be model of proposal ${shown.index + 1}`}
          highlight={[
            ...(diff?.added ?? []),
            ...(diff?.checks_added ?? []),
            ...(diff?.automated ?? []).map((a) => a + AUTOMATED_SUFFIX),
          ]}
        />
      </div>
      {(asIs.isError || toBe.isError) && <ApiProblem error={asIs.error ?? toBe.error} />}
    </section>
  );
}

function DecisionForm({ run, segment, detail }: { run: Run; segment: Segment; detail: AgentRun }) {
  const loopsLeft = (detail.state?.revision_count ?? 0) < detail.max_revision_loops;
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [comment, setComment] = useState("");
  const [analyst, setAnalyst] = usePersisted("pp-analyst", "");
  const [missing, setMissing] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const send = useMutation({
    mutationFn: () =>
      api.decide(run.run_id, segment.slug, detail.agent_run_id, {
        decision: decision as "approve" | "reject",
        comment: comment.trim() || null,
        analyst: analyst.trim() || null,
      }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const working = !!jobId && job?.status !== "failed" && job?.status !== "interrupted";

  return (
    <Coupon id="decision" label={`Your decision · round ${detail.review_round}`} meta={`applies to all ${detail.proposals.length} proposals`}>
      <p className="small muted" style={{ maxWidth: "80ch" }}>
        ProcessPilot records the decision and never executes a change.{" "}
        {loopsLeft
          ? "Rejecting with a comment asks the agent for revised proposals; rejecting without one ends the run."
          : "No revisions are left: rejecting ends this run."}
      </p>
      <form
        className="section"
        style={{ gap: 16 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (!decision) {
            setMissing(true);
            return;
          }
          send.mutate();
        }}
      >
        {/* No default: the analyst makes the call, the form never makes it for them. */}
        <div className="options" role="radiogroup" aria-label="Decision" aria-describedby={missing ? "decision-missing" : undefined}>
          <label className="option">
            <input type="radio" name="decision" checked={decision === "approve"} onChange={() => { setDecision("approve"); setMissing(false); }} />
            <span>
              <b>Approve</b>
              <p>Record these proposals as accepted for implementation planning.</p>
            </span>
          </label>
          <label className="option">
            <input type="radio" name="decision" checked={decision === "reject"} onChange={() => { setDecision("reject"); setMissing(false); }} />
            <span>
              <b>Reject</b>
              <p>{loopsLeft ? "With a comment, the agent revises; without one, the run ends." : "Ends this run."}</p>
            </span>
          </label>
        </div>
        {missing && (
          <p id="decision-missing" className="small" style={{ color: "var(--signal)" }}>
            Choose Approve or Reject first. Your comment is kept.
          </p>
        )}
        <div className="fields">
          <div className="field" style={{ gridColumn: "1 / -1" }}>
            <label htmlFor="decision-comment">Comment</label>
            <textarea
              id="decision-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="What should change, or why these proposals are acceptable"
            />
          </div>
          <div className="field">
            <label htmlFor="analyst">Your name, for the decision log</label>
            <input id="analyst" type="text" value={analyst} onChange={(e) => setAnalyst(e.target.value)} />
          </div>
        </div>
        {send.isError && <ApiProblem error={send.error} />}
        <JobLine job={job} label="Recording the decision and resuming the agent" />
        <div className="actions">
          <button className="btn primary" type="submit" disabled={send.isPending || working}>
            Submit decision
          </button>
        </div>
      </form>
    </Coupon>
  );
}

function DecisionLog({ detail }: { detail: AgentRun }) {
  // Mounted while empty, so the first decision is a change it can see land.
  const log = useRef<HTMLElement>(null);
  useStampLanding(log, detail.agent_run_id, detail.decisions.length, ".log-entry:last-child .stamp");
  if (!detail.decisions.length) return null;
  return (
    <section className="section" ref={log}>
      <div className="section-head">
        <div>
          <h2>Decision log</h2>
          <p>Every round stays on file. Rejected rounds are voided, not deleted.</p>
        </div>
      </div>
      <div className="log">
        {detail.decisions.map((d) => (
          <div key={`${d.round}-${d.decided_at}`} className={`log-entry${d.decision === "reject" ? " void" : ""}`}>
            <div>
              {d.decision === "approve" ? <Stamp tone="go">Approved</Stamp> : <Stamp tone="void">Void</Stamp>}
              <p className="small muted" style={{ marginTop: 8 }}>
                Round {d.round}
              </p>
            </div>
            <div className="section" style={{ gap: 6 }}>
              <span className="titles">{d.proposal_titles.join(" · ")}</span>
              <span className="small">
                {d.comment ? `“${d.comment}”` : <span className="muted">No comment</span>}
              </span>
            </div>
            <span className="small muted num" style={{ textAlign: "right" }}>
              {d.analyst ?? "—"}
              <br />
              {fmtStamp(d.decided_at)} UTC
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
