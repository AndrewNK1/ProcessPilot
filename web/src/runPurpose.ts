// Dev vs evaluation runs (evaluation Phase 4). Evaluation mining runs are made
// by scripts/eval and hidden on the normal pages unless the viewer asks for
// them; a run the URL names explicitly always stays reachable.

export type RunPurpose = "dev" | "evaluation";

interface PurposeRun {
  run_id: string;
  purpose?: RunPurpose;
}

/** Runs written before the field existed count as dev (the migration's backfill). */
export function purposeOf(run: PurposeRun): RunPurpose {
  return run.purpose ?? "dev";
}

export function purposeLabel(purpose: RunPurpose): string {
  return purpose === "evaluation" ? "Evaluation" : "Dev";
}

/** The runs a picker offers: dev runs, evaluation runs only when shown, and always the current one. */
export function pickableRuns<T extends PurposeRun>(runs: T[], showEvaluation: boolean, currentRunId?: string): T[] {
  return runs.filter((r) => showEvaluation || purposeOf(r) !== "evaluation" || r.run_id === currentRunId);
}

/** How many runs the toggle hides (for its label); the run on screen is never hidden. */
export function hiddenEvaluationCount(runs: PurposeRun[], showEvaluation: boolean, currentRunId?: string): number {
  return showEvaluation ? 0 : runs.filter((r) => purposeOf(r) === "evaluation" && r.run_id !== currentRunId).length;
}
