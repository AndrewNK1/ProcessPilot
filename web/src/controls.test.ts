import { describe, expect, it } from "vitest";
import type { ProposalView } from "./api";
import { lockedOutsideModel, protectedChips, requestedProtection, unverifiableWarning } from "./controls";

const FLAG = "approval/segregation of duties cannot be checked against this log";

function view(validation: ProposalView["validation"]): ProposalView {
  return {
    index: 0,
    proposal: {} as ProposalView["proposal"],
    operations_text: [],
    edit: null,
    validation,
  };
}

describe("protected activities", () => {
  it("locks the preset's activities and keeps the analyst's picks", () => {
    const chips = protectedChips(["A", "Record Goods Receipt", "C"], ["Clear Invoice", "Record Goods Receipt"], new Set(["C"]));
    expect(chips).toEqual([
      { activity: "A", checked: false, locked: false },
      { activity: "Record Goods Receipt", checked: true, locked: true },
      { activity: "C", checked: true, locked: false },
    ]);
    // Unticked in state, still checked: the floor cannot be removed.
    expect(protectedChips(["Record Goods Receipt"], ["Record Goods Receipt"], new Set())[0].checked).toBe(true);
  });

  it("sends the floor with every request and names locked activities outside the model", () => {
    expect(requestedProtection(["Clear Invoice"], new Set(["C", "Clear Invoice"]))).toEqual(["Clear Invoice", "C"]);
    expect(lockedOutsideModel(["Record Goods Receipt"], ["Clear Invoice", "Record Goods Receipt"])).toEqual(["Clear Invoice"]);
  });
});

describe("unverifiable-controls warning", () => {
  it("shows the flag and the activities that raised it", () => {
    const v = view({
      status: "validated",
      message: null,
      soundness: null,
      estimate: null,
      unverifiable_controls: [FLAG],
      unverifiable_activities: ["Change Approval for Purchase Order"],
    });
    expect(unverifiableWarning(v)).toEqual({ flags: [FLAG], activities: ["Change Approval for Purchase Order"] });
  });

  it("is absent without flags, on old runs and without validation", () => {
    expect(unverifiableWarning(view({ status: "validated", message: null, soundness: null, estimate: null }))).toBeNull();
    expect(
      unverifiableWarning(
        view({ status: "validated", message: null, soundness: null, estimate: null, unverifiable_controls: [] }),
      ),
    ).toBeNull();
    expect(unverifiableWarning(view(null))).toBeNull();
  });
});
