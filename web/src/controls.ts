// Control rules on the redesign page (evaluation Phase 3): which protected
// activities the preset locks, and the unverifiable-controls warning of a
// proposal. Pure functions so they are testable without a DOM.

import type { ProposalView } from "./api";

export interface ProtectedChip {
  activity: string;
  checked: boolean;
  // Set by the preset: shown checked and disabled; the API adds it back anyway.
  locked: boolean;
}

/** One chip per model activity; preset-protected ones are checked and locked. */
export function protectedChips(activities: string[], locked: string[], chosen: Set<string>): ProtectedChip[] {
  const floor = new Set(locked);
  return activities.map((a) => ({ activity: a, checked: floor.has(a) || chosen.has(a), locked: floor.has(a) }));
}

/** Preset-protected activities this segment's model does not contain (no chip to show). */
export function lockedOutsideModel(activities: string[], locked: string[]): string[] {
  const inModel = new Set(activities);
  return locked.filter((a) => !inModel.has(a));
}

/** The activities the analyst protects for the run: the preset's floor plus their picks. */
export function requestedProtection(locked: string[], chosen: Set<string>): string[] {
  return [...new Set([...locked, ...chosen])];
}

export interface UnverifiableWarning {
  flags: string[];
  activities: string[];
}

/** The proposal's unverifiable-controls warning, or null when it touches none. */
export function unverifiableWarning(view: ProposalView): UnverifiableWarning | null {
  const flags = view.validation?.unverifiable_controls ?? [];
  if (flags.length === 0) return null;
  return { flags, activities: view.validation?.unverifiable_activities ?? [] };
}
