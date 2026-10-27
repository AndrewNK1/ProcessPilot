// Typed client for the ProcessPilot API (src/processpilot/api/main.py).
//
// WHY the UI only talks HTTP: everything it shows must be exactly what the
// backend stored (evidence, proposals, decisions), and the review decision
// must go through the same endpoint a script or test would use. Types mirror
// the JSON the API returns; fields the UI does not read are left out.

export type Severity = "info" | "warn" | "error";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "interrupted";
export type AgentStatus = "running" | "awaiting_review" | "approved" | "rejected" | "failed";
export type EvidenceType =
  | "kpi" | "bottleneck" | "activity" | "variant" | "rework" | "resource" | "handover"
  | "conformance" | "model_conformance";

export interface Health {
  status: string;
  model_id: string | null;
  graphviz: boolean;
  active_jobs: number;
}

export interface UiSettings {
  poll_seconds: number;
  bpmn_height_px: number;
}

export interface Preset {
  name: string;
  description: string;
  has_mapping: boolean;
  source_available: boolean;
  source_problem: string | null;
}

export interface Mapping {
  case_id: string;
  activity: string;
  timestamp: string;
  resource: string | null;
  segment_by: string | null;
  timestamp_format: string | null;
  timestamp_timezone_policy: "utc" | "local" | "none";
}

export interface ProfileColumn {
  name: string;
  dtype: string;
  null_share: number;
  distinct: number;
  examples: string[];
}

export interface Profile {
  row_count: number;
  columns: ProfileColumn[];
  suggestions: Record<string, { column: string; confidence: number }[]>;
}

export interface QualityIssue {
  code: string;
  severity: Severity;
  message: string;
  count: number;
  share: number;
  example_rows: Record<string, unknown>[];
}

export interface QualityReport {
  issue_count: number;
  event_count: number;
  case_count: number;
  issues: QualityIssue[];
  cleaning_log: Record<string, unknown>[];
}

export interface CleaningAdvice {
  option: keyof CleaningPolicy;
  recommended: boolean;
  reason: string;
  cases_removed: number;
  rows_removed: number;
  event_share_removed: number;
}

export interface CleaningPolicy {
  drop_cases_with_missing_values: boolean;
  drop_cases_with_out_of_range_timestamps: boolean;
  drop_single_event_cases: boolean;
  drop_exact_duplicate_events: boolean;
}

export interface DatasetSummary {
  id: string;
  kind: "preset" | "upload";
  name: string;
  preset: string;
  source_path: string;
  created_at: string;
  mapping: Mapping | null;
}

export interface Dataset extends DatasetSummary {
  profile: Profile | null;
  quality: { report: QualityReport; recommendation?: { advice: CleaningAdvice[] } | null } | null;
  suggested_cleaning: Partial<CleaningPolicy>;
  runs: string[];
}

export interface RunSummary {
  run_id: string;
  origin: "app" | "disk";
  status: string;
  dataset_id: string | null;
  preset: string | null;
  evidence_count: number;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  segment: string;
  type: EvidenceType;
  summary: string;
  metrics: Record<string, any>;
  refs: string[];
}

export interface Segment {
  name: string;
  slug: string;
  kpi: EvidenceItem | null;
  has_model: boolean;
  agent_runs: number;
}

export interface Run extends RunSummary {
  segments: Segment[];
  data_quality: QualityReport | null;
}

export interface SegmentModel {
  segment: string;
  activities: string[];
  unmodelled_activities: string[];
  default_constraints: Constraints | null;
  preset: string | null;
}

export interface Constraints {
  text: string[];
  protected_activities: string[];
}

export interface PathStep {
  position: number;
  activity: string;
  mean_wait_seconds: number | null;
  median_wait_seconds: number | null;
  p90_wait_seconds: number | null;
}

export interface PathWaits {
  evidence_id: string;
  activities: string[];
  case_count: number;
  mean_throughput_seconds: number;
  median_throughput_seconds: number;
  steps: PathStep[];
}

export interface AgentRunSummary {
  agent_run_id: string;
  model_id: string;
  status: AgentStatus;
  created_at: string;
  updated_at: string;
}

export interface Hypothesis {
  description: string;
  evidence_ids: string[];
  confidence: "high" | "medium" | "low";
  alternative_explanations: string[];
}

export type EditOperation =
  | { op: "automate_activity"; activity: string }
  | { op: "remove_activity"; activity: string }
  | { op: "add_activity" | "add_check"; name: string; after: string }
  | { op: "reorder_activities"; activity: string; after: string }
  | { op: "parallelize_activities" | "merge_activities"; activities: string[]; name?: string };

export interface Proposal {
  title: string;
  heuristic_id: string;
  edit_operations: EditOperation[];
  evidence_ids: string[];
  rationale: string;
  expected_effect: string;
  risks: string[];
  assumptions: string[];
}

export interface Estimate {
  label: string;
  case_count: number;
  cases_affected: number;
  cases_affected_share: number;
  as_is: { median_seconds: number; mean_seconds: number; p90_seconds: number };
  to_be: { median_seconds: number; mean_seconds: number; p90_seconds: number };
  median_change_seconds: number | null;
  median_change_share: number | null;
  assumptions: string[];
  not_estimated: string[];
}

export interface ProposalView {
  index: number;
  proposal: Proposal;
  operations_text: string[];
  edit: {
    status: string;
    message: string | null;
    diff: {
      changes: string[];
      added: string[];
      removed: string[];
      checks_added: string[];
      automated: string[];
    } | null;
  } | null;
  validation: {
    status: string;
    message: string | null;
    soundness: { sound: boolean; method: string } | null;
    estimate: Estimate | null;
  } | null;
}

export interface Decision {
  round: number;
  decision: "approve" | "reject";
  comment: string | null;
  analyst: string | null;
  proposal_titles: string[];
  decided_at: string;
}

export interface AgentRun extends AgentRunSummary {
  busy: boolean;
  awaiting_review: boolean;
  review_round: number;
  max_revision_loops: number;
  state: {
    diagnosis: { hypotheses: Hypothesis[] } | null;
    revision_count: number;
    warnings: { code: string; path: string; message: string }[];
    error: string | null;
  } | null;
  proposals: ProposalView[];
  decisions: Decision[];
}

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  message: string | null;
  error: string | null;
  result: Record<string, any> | null;
}

export class ApiError extends Error {
  // `status` is null when the API could not be reached at all.
  constructor(public status: number | null, public detail: string) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "content-type": "application/json" };
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(null, "The ProcessPilot API is not reachable.");
  }
  const isXml = (response.headers.get("content-type") ?? "").includes("xml");
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* not JSON: keep the status text */
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (isXml ? await response.text() : await response.json()) as T;
}

const seg = (runId: string, slug: string) =>
  `/runs/${encodeURIComponent(runId)}/segments/${encodeURIComponent(slug)}`;

export const api = {
  health: () => request<Health>("GET", "/health"),
  uiSettings: () => request<UiSettings>("GET", "/settings/ui"),
  presets: () => request<Preset[]>("GET", "/presets"),
  datasets: () => request<DatasetSummary[]>("GET", "/datasets"),
  dataset: (id: string) => request<Dataset>("GET", `/datasets/${encodeURIComponent(id)}`),
  createPresetDataset: (preset: string) => request<Dataset>("POST", "/datasets/preset", { preset }),
  uploadDataset: (file: File, basePreset: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("base_preset", basePreset);
    return request<Dataset>("POST", "/datasets/upload", form);
  },
  profile: (id: string) => request<{ job_id: string }>("POST", `/datasets/${encodeURIComponent(id)}/profile`),
  setMapping: (id: string, mapping: Mapping) =>
    request<Dataset>("PUT", `/datasets/${encodeURIComponent(id)}/mapping`, mapping),
  validate: (id: string) => request<{ job_id: string }>("POST", `/datasets/${encodeURIComponent(id)}/validate`),
  mine: (id: string, policy: CleaningPolicy) =>
    request<{ job_id: string; run_id: string }>("POST", `/datasets/${encodeURIComponent(id)}/runs`, policy),
  runs: () => request<RunSummary[]>("GET", "/runs"),
  run: (runId: string) => request<Run>("GET", `/runs/${encodeURIComponent(runId)}`),
  setRunPreset: (runId: string, preset: string) =>
    request<Run>("PUT", `/runs/${encodeURIComponent(runId)}/preset`, { preset }),
  evidence: (runId: string, slug: string) => request<EvidenceItem[]>("GET", `${seg(runId, slug)}/evidence`),
  model: (runId: string, slug: string) => request<SegmentModel>("GET", `${seg(runId, slug)}/model`),
  asIsBpmn: (runId: string, slug: string) => request<string>("GET", `${seg(runId, slug)}/as-is.bpmn`),
  pathWaits: (runId: string, slug: string, rank: number) =>
    request<PathWaits>("GET", `${seg(runId, slug)}/variants/${rank}/waits`),
  agentRuns: (runId: string, slug: string) => request<AgentRunSummary[]>("GET", `${seg(runId, slug)}/agent-runs`),
  agentRun: (runId: string, slug: string, agentRunId: string) =>
    request<AgentRun>("GET", `${seg(runId, slug)}/agent-runs/${encodeURIComponent(agentRunId)}`),
  startAgent: (runId: string, slug: string, constraints: Constraints, modelId: string | null) =>
    request<{ job_id: string }>("POST", `${seg(runId, slug)}/agent-runs`, { constraints, model_id: modelId }),
  toBeBpmn: (runId: string, slug: string, agentRunId: string, index: number) =>
    request<string>(
      "GET",
      `${seg(runId, slug)}/agent-runs/${encodeURIComponent(agentRunId)}/proposals/${index}/to-be.bpmn`,
    ),
  decide: (
    runId: string,
    slug: string,
    agentRunId: string,
    body: { decision: "approve" | "reject"; comment: string | null; analyst: string | null },
  ) =>
    request<{ job_id: string }>(
      "POST",
      `${seg(runId, slug)}/agent-runs/${encodeURIComponent(agentRunId)}/decision`,
      body,
    ),
  job: (jobId: string) => request<Job>("GET", `/jobs/${encodeURIComponent(jobId)}`),
};
