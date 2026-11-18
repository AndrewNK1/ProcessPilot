import { describe, expect, it } from "vitest";
import { hiddenEvaluationCount, pickableRuns, purposeLabel, purposeOf } from "./runPurpose";

const runs = [
  { run_id: "r3", purpose: "evaluation" as const },
  { run_id: "r2", purpose: "dev" as const },
  { run_id: "r1" }, // written before the field existed
];

describe("run purpose", () => {
  it("treats runs without the field as dev", () => {
    expect(purposeOf(runs[2])).toBe("dev");
    expect(purposeLabel("evaluation")).toBe("Evaluation");
    expect(purposeLabel("dev")).toBe("Dev");
  });

  it("hides evaluation runs by default but keeps the one the URL names", () => {
    expect(pickableRuns(runs, false).map((r) => r.run_id)).toEqual(["r2", "r1"]);
    expect(pickableRuns(runs, false, "r3").map((r) => r.run_id)).toEqual(["r3", "r2", "r1"]);
    expect(pickableRuns(runs, true).map((r) => r.run_id)).toEqual(["r3", "r2", "r1"]);
  });

  it("counts what the toggle hides", () => {
    expect(hiddenEvaluationCount(runs, false)).toBe(1);
    expect(hiddenEvaluationCount(runs, true)).toBe(0);
  });
});
