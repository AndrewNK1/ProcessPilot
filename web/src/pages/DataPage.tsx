// Data: choose an event log, confirm how its columns map, read the data-quality
// report, choose what to clean, mine. The order is the method's: nothing is
// mined before the analyst has seen the mapping and the report, and nothing is
// removed unless the analyst ticks it (the recommendation is only the default).

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, FileUp } from "lucide-react";
import {
  api,
  type CleaningPolicy,
  type Dataset,
  type Mapping,
  type Profile,
  type QualityReport,
} from "../api";
import { fmtInt, fmtShare, fmtStamp } from "../format";
import { useJob, writeScope } from "../hooks";
import { ApiProblem, Coupon, JobLine, Kpis, Notice, Seg } from "../ui";

const ROLES_REQUIRED = ["case_id", "activity", "timestamp"] as const;
const ROLES_OPTIONAL = ["resource", "segment_by"] as const;
const ROLE_LABELS: Record<string, string> = {
  case_id: "Case id",
  activity: "Activity",
  timestamp: "Timestamp",
  resource: "Resource",
  segment_by: "Segment by",
  timestamp_format: "Timestamp format",
  timestamp_timezone_policy: "Timezone policy",
};
const ROLE_HINTS: Record<string, string> = {
  resource: "Optional. Without it, resource and handover analysis is skipped and says so.",
  segment_by: "Optional. A case-level attribute; each value is mined separately.",
};
const CLEANING_LABELS: Record<keyof CleaningPolicy, string> = {
  drop_cases_with_missing_values: "Drop cases with a missing case id, activity or timestamp",
  drop_cases_with_out_of_range_timestamps: "Drop cases with timestamps outside the plausible range",
  drop_single_event_cases: "Drop cases with fewer events than the preset's minimum",
  drop_exact_duplicate_events: "Drop exact duplicate events (keeps the first)",
};
const UPLOAD_TYPES = ".csv,.xes,.xlsx,.xls,.parquet";

export function DataPage() {
  const [params, setParams] = useSearchParams();
  const datasetId = params.get("dataset");
  const dataset = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api.dataset(datasetId as string),
    enabled: !!datasetId,
  });
  const choose = (id: string | null) => setParams(id ? { dataset: id } : {});
  const ds = dataset.data;
  const hasMapping = !!ds?.mapping;
  const hasQuality = !!ds?.quality;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Data</h1>
          <p className="finding">
            Choose an event log, confirm how its columns map, read its quality report, then mine it.
            Nothing is removed unless you tick it.
          </p>
        </div>
      </div>
      {dataset.isError && <ApiProblem error={dataset.error} />}

      <Coupon
        no={1}
        label="Event log"
        quiet={!!ds}
        meta={ds ? `${ds.kind} · ${ds.name}` : "Open"}
      >
        {ds ? (
          <div className="summary-line">
            <div style={{ minWidth: 0 }}>
              <b>{ds.name}</b>
              <p className="small muted" style={{ overflowWrap: "anywhere" }}>
                Settings preset {ds.preset} · {ds.source_path}
              </p>
            </div>
            <button className="btn" type="button" onClick={() => choose(null)}>
              Choose another log
            </button>
          </div>
        ) : (
          <ChooseLog onChosen={choose} />
        )}
      </Coupon>

      {/* A leg not reached yet is only its head: the wallet shows what remains. */}
      <Coupon no={2} label="Column mapping" quiet={hasMapping || !ds} meta={!ds ? "Opens once a log is chosen" : hasMapping ? "Confirmed" : "Open"}>
        {ds && <MappingStep dataset={ds} />}
      </Coupon>

      <Coupon
        no={3}
        label="Data quality"
        quiet={hasQuality || !hasMapping}
        meta={!hasMapping ? "Opens once the mapping is confirmed" : hasQuality ? `${ds?.quality?.report.issue_count} findings` : "Open"}
      >
        {hasMapping && ds && <QualityStep dataset={ds} />}
      </Coupon>

      <Coupon
        no={4}
        label="Clean and mine"
        quiet={!hasQuality}
        meta={!hasQuality ? "Opens once the quality report is ready" : ds?.runs.length ? `${ds.runs.length} mined` : "Open"}
      >
        {hasQuality && ds && <MineStep dataset={ds} />}
      </Coupon>
    </>
  );
}

// --- 1. choose a log ------------------------------------------------------------------

type Source = "preset" | "upload" | "earlier";

function ChooseLog({ onChosen }: { onChosen: (id: string) => void }) {
  const [source, setSource] = useState<Source>("preset");
  return (
    <>
      <Seg<Source>
        label="Start from"
        value={source}
        onChange={setSource}
        options={[
          { id: "preset", label: "Configured preset" },
          { id: "upload", label: "Upload a file" },
          { id: "earlier", label: "Earlier dataset" },
        ]}
      />
      {source === "preset" && <PresetChoice onChosen={onChosen} />}
      {source === "upload" && <UploadChoice onChosen={onChosen} />}
      {source === "earlier" && <EarlierChoice onChosen={onChosen} />}
    </>
  );
}

function PresetChoice({ onChosen }: { onChosen: (id: string) => void }) {
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets });
  const usable = (presets.data ?? []).filter((p) => p.source_available && p.has_mapping);
  const unavailable = (presets.data ?? []).filter((p) => p.has_mapping && !p.source_available);
  const [name, setName] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (preset: string) => api.createPresetDataset(preset),
    onSuccess: (ds) => onChosen(ds.id),
  });
  const chosen = name ?? usable[0]?.name ?? null;
  if (presets.isError) return <ApiProblem error={presets.error} />;
  return (
    <form
      className="section"
      onSubmit={(e) => {
        e.preventDefault();
        if (chosen) create.mutate(chosen);
      }}
    >
      {usable.length === 0 && !presets.isLoading ? (
        <Notice tone="wait">No preset has a reachable source file. Upload a file instead.</Notice>
      ) : (
        <div className="options" role="radiogroup" aria-label="Preset">
          {usable.map((p) => (
            <label key={p.name} className="option">
              <input type="radio" name="preset" checked={chosen === p.name} onChange={() => setName(p.name)} />
              <span>
                <b>{p.name}</b>
                <p>{p.description}</p>
              </span>
            </label>
          ))}
        </div>
      )}
      {create.isError && <ApiProblem error={create.error} />}
      <div className="actions">
        <button className="btn primary" type="submit" disabled={!chosen || create.isPending}>
          Use this preset <ArrowRight size={16} />
        </button>
      </div>
      {unavailable.length > 0 && (
        <details className="disclosure">
          <summary>
            <ChevronRight size={16} /> Presets whose source file is not available
            <span>{unavailable.length}</span>
          </summary>
          <div className="disclosure-body small">
            {unavailable.map((p) => (
              <p key={p.name}>
                <b>{p.name}</b>: {p.source_problem}
              </p>
            ))}
          </div>
        </details>
      )}
    </form>
  );
}

function UploadChoice({ onChosen }: { onChosen: (id: string) => void }) {
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.presets });
  const names = (presets.data ?? []).map((p) => p.name);
  const [file, setFile] = useState<File | null>(null);
  const [base, setBase] = useState("generic");
  const [over, setOver] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const job = useJob(jobId, (j) => j.status === "succeeded" && datasetId && onChosen(datasetId));
  const upload = useMutation({
    mutationFn: async () => {
      const ds = await api.uploadDataset(file as File, base);
      setDatasetId(ds.id);
      return api.profile(ds.id);
    },
    onSuccess: ({ job_id }) => setJobId(job_id),
  });
  return (
    <form
      className="section"
      onSubmit={(e) => {
        e.preventDefault();
        if (file) upload.mutate();
      }}
    >
      <label
        className={`drop${over ? " over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
        }}
      >
        <FileUp size={22} aria-hidden style={{ color: "var(--carbon)" }} />
        <span>
          <b>{file ? file.name : "Drop an event log here, or choose a file"}</b>
          <span className="small muted" style={{ display: "block" }}>
            {file ? `${fmtInt(file.size / 1024)} KB` : "CSV, XES, Excel or Parquet"}
          </span>
        </span>
        <input
          type="file"
          accept={UPLOAD_TYPES}
          className="visually-hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </label>
      <div className="field" style={{ maxWidth: 360 }}>
        <label htmlFor="base-preset">Settings preset</label>
        <select id="base-preset" value={base} onChange={(e) => setBase(e.target.value)}>
          {names.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
        <span className="hint">Rules and thresholds come from this preset; the column mapping comes from you.</span>
      </div>
      {upload.isError && <ApiProblem error={upload.error} />}
      <JobLine job={job} label="Profiling the file" />
      <div className="actions">
        <button className="btn primary" type="submit" disabled={!file || upload.isPending || !!jobId}>
          Upload and profile
        </button>
      </div>
    </form>
  );
}

function EarlierChoice({ onChosen }: { onChosen: (id: string) => void }) {
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  const [id, setId] = useState<string | null>(null);
  if (datasets.isError) return <ApiProblem error={datasets.error} />;
  const list = datasets.data ?? [];
  if (!datasets.isLoading && list.length === 0) return <p className="empty">No earlier dataset yet.</p>;
  const chosen = id ?? list[0]?.id ?? null;
  return (
    <form
      className="section"
      onSubmit={(e) => {
        e.preventDefault();
        if (chosen) onChosen(chosen);
      }}
    >
      <div className="options" role="radiogroup" aria-label="Dataset" style={{ maxHeight: 320, overflowY: "auto" }}>
        {list.map((d) => (
          <label key={d.id} className="option">
            <input type="radio" name="dataset" checked={chosen === d.id} onChange={() => setId(d.id)} />
            <span>
              <b>{d.name}</b>
              <p className="num">
                {d.kind} · {fmtStamp(d.created_at)} UTC
              </p>
            </span>
          </label>
        ))}
      </div>
      <div className="actions">
        <button className="btn primary" type="submit" disabled={!chosen}>
          Continue with this dataset <ArrowRight size={16} />
        </button>
      </div>
    </form>
  );
}

// --- 2. mapping -----------------------------------------------------------------------

function MappingStep({ dataset }: { dataset: Dataset }) {
  const [editing, setEditing] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const profile = useMutation({ mutationFn: () => api.profile(dataset.id), onSuccess: (r) => setJobId(r.job_id) });

  if (dataset.kind === "preset" && dataset.mapping) {
    return (
      <>
        <p className="small muted">The preset fixes the mapping; this is what each column means.</p>
        <MappingTable mapping={dataset.mapping} />
      </>
    );
  }
  if (!dataset.profile) {
    return (
      <>
        <p className="small muted">The file must be profiled before its columns can be mapped.</p>
        <JobLine job={job} label="Profiling the file" />
        {profile.isError && <ApiProblem error={profile.error} />}
        <div className="actions">
          <button className="btn primary" type="button" onClick={() => profile.mutate()} disabled={!!jobId}>
            Profile the file
          </button>
        </div>
      </>
    );
  }
  if (dataset.mapping && !editing) {
    return (
      <div className="summary-line" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 520px", minWidth: 0 }}>
          <MappingTable mapping={dataset.mapping} />
        </div>
        <button className="btn" type="button" onClick={() => setEditing(true)}>
          Change the mapping
        </button>
      </div>
    );
  }
  return <MappingForm dataset={dataset} profile={dataset.profile} onDone={() => setEditing(false)} />;
}

function MappingTable({ mapping }: { mapping: Mapping }) {
  return (
    <dl className="fields-sheet" style={{ margin: 0 }}>
      {Object.entries(ROLE_LABELS).map(([role, label]) =>
        role in mapping ? (
          <div key={role}>
            <dt>{label}</dt>
            <dd>{(mapping as any)[role] ?? <span className="muted">—</span>}</dd>
          </div>
        ) : null,
      )}
    </dl>
  );
}

function MappingForm({ dataset, profile, onDone }: { dataset: Dataset; profile: Profile; onDone: () => void }) {
  const client = useQueryClient();
  const current = dataset.mapping;
  const columns = profile.columns.map((c) => c.name);
  const initial = useMemo(() => {
    const out: Record<string, string> = {};
    for (const role of [...ROLES_REQUIRED, ...ROLES_OPTIONAL]) {
      const ranked = profile.suggestions[role] ?? [];
      const preset = current ? ((current as any)[role] as string | null) : null;
      out[role] = preset ?? (ROLES_REQUIRED.includes(role as any) ? ranked[0]?.column ?? "" : "");
    }
    return out;
  }, [profile, current]);
  const [roles, setRoles] = useState(initial);
  const [format, setFormat] = useState(current?.timestamp_format ?? "");
  const [policy, setPolicy] = useState<Mapping["timestamp_timezone_policy"]>(
    current?.timestamp_timezone_policy ?? "utc",
  );
  const save = useMutation({
    mutationFn: (mapping: Mapping) => api.setMapping(dataset.id, mapping),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      onDone();
    },
  });
  const missing = ROLES_REQUIRED.filter((r) => !roles[r]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (missing.length) return;
    save.mutate({
      case_id: roles.case_id,
      activity: roles.activity,
      timestamp: roles.timestamp,
      resource: roles.resource || null,
      segment_by: roles.segment_by || null,
      timestamp_format: format.trim() || null,
      timestamp_timezone_policy: policy,
    });
  };

  return (
    <form className="section" style={{ gap: 20 }} onSubmit={submit}>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th className="r">Empty</th>
              <th className="r">Distinct</th>
              <th className="wide hide-sm">Examples</th>
            </tr>
          </thead>
          <tbody>
            {profile.columns.map((c) => (
              <tr key={c.name}>
                <td>{c.name}</td>
                <td className="muted">{c.dtype}</td>
                <td className="r">{fmtShare(c.null_share)}</td>
                <td className="r">{fmtInt(c.distinct)}</td>
                <td className="muted hide-sm" style={{ overflowWrap: "anywhere" }}>
                  {c.examples.join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">
        {fmtInt(profile.row_count)} rows. Suggestions are ranked by name and content; nothing is picked without you.
      </p>
      <div className="fields">
        {[...ROLES_REQUIRED, ...ROLES_OPTIONAL].map((role) => {
          const ranked = profile.suggestions[role] ?? [];
          const confidence = new Map(ranked.map((s) => [s.column, s.confidence]));
          const ordered = [...ranked.map((s) => s.column), ...columns.filter((c) => !confidence.has(c))];
          const optional = ROLES_OPTIONAL.includes(role as any);
          return (
            <div className="field" key={role}>
              <label htmlFor={`map-${role}`}>{ROLE_LABELS[role]}</label>
              <select
                id={`map-${role}`}
                value={roles[role]}
                onChange={(e) => setRoles({ ...roles, [role]: e.target.value })}
                aria-describedby={ROLE_HINTS[role] ? `hint-${role}` : undefined}
              >
                {(optional || !roles[role]) && <option value="">{optional ? "None" : "Choose a column"}</option>}
                {ordered.map((c) => (
                  <option key={c} value={c}>
                    {confidence.has(c) ? `${c} · suggested, ${fmtShare(confidence.get(c), 0)}` : c}
                  </option>
                ))}
              </select>
              {ROLE_HINTS[role] && (
                <span className="hint" id={`hint-${role}`}>
                  {ROLE_HINTS[role]}
                </span>
              )}
            </div>
          );
        })}
        <div className="field">
          <label htmlFor="map-format">Timestamp format</label>
          <input
            id="map-format"
            type="text"
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            placeholder="Detect automatically"
            aria-describedby="hint-format"
          />
          <span className="hint" id="hint-format">
            Optional, in strftime form, e.g. %d.%m.%Y %H:%M.
          </span>
        </div>
        <div className="field">
          <label htmlFor="map-tz">Timezone policy</label>
          <select id="map-tz" value={policy} onChange={(e) => setPolicy(e.target.value as Mapping["timestamp_timezone_policy"])}>
            <option value="utc">utc: convert offsets to UTC, then drop them (recommended)</option>
            <option value="local">local: keep local times; mixed offsets fail</option>
            <option value="none">none: reject timestamps with offsets</option>
          </select>
        </div>
      </div>
      {save.isError && <ApiProblem error={save.error} />}
      <div className="actions">
        <button className="btn primary" type="submit" disabled={missing.length > 0 || save.isPending}>
          Confirm mapping
        </button>
        {missing.length > 0 && (
          <span className="small muted">Choose a column for {missing.map((r) => ROLE_LABELS[r]).join(", ")}.</span>
        )}
      </div>
    </form>
  );
}

// --- 3. quality -----------------------------------------------------------------------

function QualityStep({ dataset }: { dataset: Dataset }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const validate = useMutation({ mutationFn: () => api.validate(dataset.id), onSuccess: (r) => setJobId(r.job_id) });
  const report = dataset.quality?.report;
  if (!report) {
    return (
      <>
        <p className="small muted">
          Checks missing values, unparseable and implausible timestamps, duplicates, single-event cases and more. It
          only reports; nothing in the data is changed.
        </p>
        <JobLine job={job} label="Checking data quality" />
        {validate.isError && <ApiProblem error={validate.error} />}
        <div className="actions">
          <button className="btn primary" type="button" onClick={() => validate.mutate()} disabled={!!jobId && job?.status !== "failed"}>
            Check data quality
          </button>
        </div>
      </>
    );
  }
  return <QualityReportView report={report} />;
}

export function QualityReportView({ report }: { report: QualityReport }) {
  const count = (s: string) => report.issues.filter((i) => i.severity === s).length;
  const examples = report.issues.filter((i) => i.example_rows.length);
  return (
    <>
      <Kpis
        cells={[
          { label: "Events", value: fmtInt(report.event_count) },
          { label: "Cases", value: fmtInt(report.case_count) },
          { label: "Errors", value: fmtInt(count("error")) },
          { label: "Warnings", value: fmtInt(count("warn")) },
          { label: "Notes", value: fmtInt(count("info")) },
        ]}
      />
      {report.issues.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Severity</th>
                <th className="wide">Finding</th>
                <th className="r">Count</th>
                <th className="r">Share of events</th>
              </tr>
            </thead>
            <tbody>
              {report.issues.map((i) => (
                <tr key={i.code}>
                  <td>
                    <span className={`sev ${i.severity}`}>{i.severity === "warn" ? "warning" : i.severity === "info" ? "note" : "error"}</span>
                  </td>
                  <td>
                    {i.message}
                    <div className="small muted">{i.code}</div>
                  </td>
                  <td className="r">{fmtInt(i.count)}</td>
                  <td className="r">{fmtShare(i.share, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">No issues found.</p>
      )}
      {examples.length > 0 && (
        <details className="disclosure">
          <summary>
            <ChevronRight size={16} /> Example rows <span>{examples.length} findings</span>
          </summary>
          <div className="disclosure-body">
            {examples.map((issue) => (
              <div key={issue.code} className="section" style={{ gap: 6 }}>
                <h3>{issue.code}</h3>
                <div className="table-wrap">
                  <table className="data small">
                    <thead>
                      <tr>
                        {Object.keys(issue.example_rows[0]).map((k) => (
                          <th key={k}>{k}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {issue.example_rows.map((row, n) => (
                        <tr key={n}>
                          {Object.values(row).map((v, m) => (
                            <td key={m}>{v === null ? <span className="muted">—</span> : String(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
      <p className="small muted">The report only reports. Nothing has been changed in the data.</p>
    </>
  );
}

// --- 4. clean and mine ----------------------------------------------------------------

type Start = "recommended" | "preset" | "none";

function MineStep({ dataset }: { dataset: Dataset }) {
  const advice = new Map((dataset.quality?.recommendation?.advice ?? []).map((a) => [a.option, a]));
  const [start, setStart] = useState<Start>(advice.size ? "recommended" : "preset");
  const defaults = (s: Start): CleaningPolicy => {
    const from: Partial<CleaningPolicy> =
      s === "recommended"
        ? Object.fromEntries([...advice].map(([k, a]) => [k, a.recommended]))
        : s === "preset"
          ? dataset.suggested_cleaning
          : {};
    return Object.fromEntries(
      (Object.keys(CLEANING_LABELS) as (keyof CleaningPolicy)[]).map((k) => [k, !!from[k]]),
    ) as unknown as CleaningPolicy;
  };
  const [policy, setPolicy] = useState<CleaningPolicy>(() => defaults(start));
  useEffect(() => setPolicy(defaults(start)), [start]); // eslint-disable-line react-hooks/exhaustive-deps
  const [jobId, setJobId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const job = useJob(jobId, (j) => {
    if (j.status === "succeeded" && runId) writeScope({ runId });
  });
  const mine = useMutation({
    mutationFn: () => api.mine(dataset.id, policy),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setRunId(r.run_id);
    },
  });
  const differs = advice.size > 0 && [...advice].some(([k, a]) => policy[k] !== a.recommended);
  const done = job?.status === "succeeded" && runId;

  return (
    <form
      className="section"
      style={{ gap: 18 }}
      onSubmit={(e) => {
        e.preventDefault();
        mine.mutate();
      }}
    >
      <div className="field">
        <span className="label">Start from</span>
        <Seg<Start>
          label="Start from"
          value={start}
          onChange={setStart}
          options={[
            ...(advice.size ? [{ id: "recommended" as Start, label: "ProcessPilot's recommendation" }] : []),
            { id: "preset", label: `The ${dataset.preset} preset` },
            { id: "none", label: "Remove nothing" },
          ]}
        />
        {!advice.size && <span className="hint">Run the data-quality check again to get ProcessPilot's recommendation.</span>}
      </div>
      <div className="check-list">
        {(Object.keys(CLEANING_LABELS) as (keyof CleaningPolicy)[]).map((key) => {
          const tip = advice.get(key);
          return (
            <div key={key}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={policy[key]}
                  onChange={(e) => setPolicy({ ...policy, [key]: e.target.checked })}
                />
                <span>{CLEANING_LABELS[key]}</span>
              </label>
              {tip && (
                <p className="advice">
                  <b>{tip.recommended ? "Recommended." : "Not recommended."}</b> {tip.reason}
                  {tip.rows_removed > 0 &&
                    ` Alone, this removes ${fmtInt(tip.cases_removed)} cases, ${fmtInt(tip.rows_removed)} events (${fmtShare(tip.event_share_removed)}).`}
                </p>
              )}
            </div>
          );
        })}
      </div>
      {differs && <Notice>Your selection differs from ProcessPilot's recommendation.</Notice>}
      <p className="small muted">Every removal is logged in the run.</p>
      {mine.isError && <ApiProblem error={mine.error} />}
      <JobLine job={job} label={`Mining run ${runId ?? ""}`} />
      {done ? (
        <div className="notice" style={{ background: "var(--go-sheet)", borderColor: "transparent" }}>
          <div className="summary-line" style={{ width: "100%" }}>
            <span>
              <b>Run {runId} is mined.</b> Its evidence is ready.
            </span>
            <Link className="btn primary" to={`/evidence/${runId}`}>
              Open the evidence <ArrowRight size={16} />
            </Link>
          </div>
        </div>
      ) : (
        <div className="actions">
          <button className="btn primary" type="submit" disabled={mine.isPending || (!!jobId && job?.status !== "failed")}>
            Mine this log
          </button>
          {dataset.runs.length > 0 && (
            <span className="small muted">
              Mined before as{" "}
              {dataset.runs.map((r, i) => (
                <span key={r}>
                  {i > 0 && ", "}
                  <Link to={`/evidence/${r}`}>{r}</Link>
                </span>
              ))}
            </span>
          )}
        </div>
      )}
    </form>
  );
}
