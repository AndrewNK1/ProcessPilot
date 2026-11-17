// Small shared components: the ticket wallet's coupon, tag, stamp and strip.

import type { ReactNode } from "react";
import { AlertTriangle, CircleAlert, Info, ServerOff } from "lucide-react";
import { ApiError, type AgentStatus, type EvidenceItem, type Job, type ResourceTypeBreakdown } from "./api";
import { fmtInt, shortId } from "./format";
import { resourceTypeRows } from "./resourceTypes";

export function Coupon({
  no,
  label,
  meta,
  quiet,
  children,
  stub,
  id,
}: {
  no?: number | string;
  label: ReactNode;
  meta?: ReactNode;
  quiet?: boolean;
  children?: ReactNode;
  stub?: ReactNode;
  id?: string;
}) {
  return (
    <section className="coupon" id={id} aria-label={typeof label === "string" ? label : undefined}>
      <header className={`coupon-head${quiet ? " quiet" : ""}`}>
        <span style={{ display: "inline-flex", alignItems: "center", minWidth: 0 }}>
          {no !== undefined && <span className="leg-no">{no}</span>}
          <span className="caps">{label}</span>
        </span>
        {meta && <span className="head-meta">{meta}</span>}
      </header>
      {children && <div className="coupon-body">{children}</div>}
      {stub && <div className="stub">{stub}</div>}
    </section>
  );
}

/** An evidence ID; with the item, its code-written summary is the tooltip. */
export function Tag({ id, item, missing }: { id: string; item?: EvidenceItem; missing?: boolean }) {
  return (
    <span className={`tag${missing ? " missing" : ""}`} title={item ? `${id}: ${item.summary}` : id}>
      {shortId(id)}
    </span>
  );
}

const STAMPS: Record<AgentStatus, [string, string]> = {
  running: ["In progress", "plain"],
  awaiting_review: ["Awaiting decision", "wait"],
  approved: ["Approved", "go"],
  rejected: ["Rejected", "void"],
  failed: ["Failed", "void"],
};

export function StatusStamp({ status, tilt }: { status: AgentStatus; tilt?: boolean }) {
  const [text, tone] = STAMPS[status] ?? [status, "plain"];
  return <span className={`stamp ${tone}${tilt ? " tilt" : ""}`}>{text}</span>;
}

export function Stamp({ tone, children, tilt }: { tone: "go" | "void" | "wait" | "plain"; children: ReactNode; tilt?: boolean }) {
  return <span className={`stamp ${tone}${tilt ? " tilt" : ""}`}>{children}</span>;
}

export interface KpiCell {
  label: string;
  value: string;
  unit?: string;
}

export function Kpis({ cells }: { cells: KpiCell[] }) {
  return (
    <div className="kpis" style={{ ["--n" as string]: cells.length }}>
      {cells.map((c) => (
        <div key={c.label}>
          <small>{c.label}</small>
          <span>
            <b>{c.value}</b>
            {c.unit && <em>{c.unit}</em>}
          </span>
        </div>
      ))}
    </div>
  );
}

// Human / batch / unassigned share of the log, as in the data-quality report.
export function ResourceTypesTable({ breakdown }: { breakdown: ResourceTypeBreakdown }) {
  const rows = resourceTypeRows(breakdown);
  return (
    <div className="section" style={{ gap: 6 }}>
      <h3>Resource types</h3>
      <p className="small muted" style={{ maxWidth: "80ch" }}>
        Only people are ranked by workload, and only a change between two people counts as a handover. {" "}
        {breakdown.configured ? `Rules: ${breakdown.rule_text}.` : `No rules configured: ${breakdown.rule_text}.`}
        {breakdown.defaulted_event_count > 0 &&
          ` ${fmtInt(breakdown.defaulted_event_count)} events matched no rule (e.g. ${breakdown.defaulted_values.join(", ")}).`}
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Type</th>
              <th className="r">Events</th>
              <th className="r">Share of events</th>
              <th className="r">Cases</th>
              <th className="r">Distinct values</th>
              <th className="wide">Most frequent values</th>
              <th>Decided by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.type}>
                <td>{r.label}</td>
                <td className="r">{r.events}</td>
                <td className="r">{r.eventShare}</td>
                <td className="r">
                  {r.cases} <span className="small muted">({r.caseShare})</span>
                </td>
                <td className="r">{r.distinct}</td>
                <td className="num small">{r.examples}</td>
                <td className="small muted">{r.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Notice({ tone, children }: { tone?: "error" | "wait"; children: ReactNode }) {
  const Icon = tone === "error" ? CircleAlert : tone === "wait" ? AlertTriangle : Info;
  return (
    <div className={`notice${tone ? ` ${tone}` : ""}`} role={tone === "error" ? "alert" : undefined}>
      <Icon size={16} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

/** An API failure, worded so the analyst can act on it. */
export function ApiProblem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === null) {
    return (
      <div className="notice error" role="alert">
        <ServerOff size={16} aria-hidden />
        <div>
          <b>The API is not reachable.</b> Start it with <code>.\.venv\python.exe -m processpilot.api</code>, or
          run <code>scripts\run_app.ps1</code>.
        </div>
      </div>
    );
  }
  const detail = error instanceof ApiError ? `${error.detail} (${error.status})` : String(error);
  return <Notice tone="error">The API refused this: {detail}</Notice>;
}

export function JobLine({ job, label }: { job: Job | undefined; label: string }) {
  if (!job) return null;
  if (job.status === "failed" || job.status === "interrupted") {
    return (
      <Notice tone="error">
        <b>{label} {job.status}.</b> {job.error ?? ""}
      </Notice>
    );
  }
  if (job.status === "succeeded") return null;
  return (
    <div className="progress" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <span>
        <b>{label}</b>
        {job.message ? ` · ${job.message}` : job.status === "queued" ? " · queued" : ""}
      </span>
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: T; label: string; count?: number }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          type="button"
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Seg<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { id: T; label: ReactNode }[];
  value: T | null;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
