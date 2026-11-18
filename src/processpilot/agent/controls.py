"""Control rules from the preset's ``redesign`` block: protection and unverifiable controls.

WHY these rules are parsed into one model here and not read ad hoc: the agent
runner, the API and the nodes all need the same answer to "what is protected
in this segment" and "does this proposal touch a control the log cannot
show". Two copies of that parsing would drift; a misspelt key would silently
switch a control off. ``extra="forbid"`` makes a typo fail at load.

Protected activities (evaluation Phase 3, user decision B): the preset's list
for a segment is a **floor**. The analyst may protect more activities for a
run but cannot remove one the preset protects; ``with_floor`` merges them, and
the nodes union the floor in again before every check, so a state built some
other way is still enforced. ``protected_activities`` apply to every segment;
``protected_activities_per_segment`` adds to them for the named segments (it
never replaces the common list, so a control protected everywhere cannot be
dropped for one segment by accident).

Unverifiable controls (Phase 3): some controls cannot be checked against the
log at all; BPI 2019 was published with its approval steps removed, so a
proposal touching an approval or release step cannot be checked for
segregation of duties. Such a proposal is *flagged*, not blocked. An
operation "touches" an activity when it names it in any role, including the
``after`` anchor of an add or reorder, or introduces a new activity whose name
matches (user decision A): a step inserted right after an approval changes
that approval's surroundings just as much as an edit of the approval itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from processpilot.agent.state import Constraints
from processpilot.redesign.operations import EditOperation, existing_activities, new_activities


class UnverifiableControl(BaseModel):
    """One kind of control the log cannot verify, and how to recognise its activities."""

    model_config = ConfigDict(extra="forbid")

    patterns: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    flag: str = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def _compiles(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            try:
                re.compile(p)
            except re.error as exc:  # not a ValueError, so pydantic would not wrap it
                raise ValueError(f"invalid pattern {p!r}: {exc}") from exc
        return patterns

    def matches(self, activity: str) -> bool:
        """Exact name in ``activities`` or a case-insensitive search hit of any pattern."""
        return activity in self.activities or any(
            re.search(p, activity, flags=re.IGNORECASE) for p in self.patterns
        )


class ProposalFlags(BaseModel):
    """The unverifiable-control flags of one proposal (empty lists = none)."""

    flags: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)


class ControlRules(BaseModel):
    """The ``redesign`` block of a dataset preset."""

    model_config = ConfigDict(extra="forbid")

    protected_activities: list[str] = Field(default_factory=list)
    protected_activities_per_segment: dict[str, list[str]] = Field(default_factory=dict)
    constraints_text: list[str] = Field(default_factory=list)
    unverifiable_controls: dict[str, UnverifiableControl] = Field(default_factory=dict)

    @classmethod
    def from_preset(cls, preset: dict[str, object]) -> ControlRules:
        """Parse the preset's ``redesign`` block (absent = no rules)."""
        return cls.model_validate(preset.get("redesign") or {})

    def check_segments(self, segments: Iterable[str]) -> None:
        """Raise if a per-segment key names no segment of the run (likely a typo)."""
        known = set(segments)
        unknown = sorted(set(self.protected_activities_per_segment) - known)
        if unknown:
            raise ValueError(
                f"redesign.protected_activities_per_segment names segment(s) {unknown} "
                f"that this run does not have (segments: {sorted(known)})"
            )

    def protected_for(self, segment: str) -> list[str]:
        """The preset's protected activities for ``segment``: common list, then additions."""
        extra = self.protected_activities_per_segment.get(segment, [])
        return list(dict.fromkeys([*self.protected_activities, *extra]))

    def defaults_for(self, segment: str) -> Constraints:
        """The constraints an analyst starts from for ``segment``."""
        return Constraints(
            text=list(self.constraints_text), protected_activities=self.protected_for(segment)
        )

    def with_floor(self, segment: str, requested: Constraints | None) -> Constraints:
        """``requested`` (or the defaults) with the preset's protection added back in."""
        if requested is None:
            return self.defaults_for(segment)
        protected = dict.fromkeys([*self.protected_for(segment), *requested.protected_activities])
        return Constraints(text=list(requested.text), protected_activities=list(protected))

    def flag_proposal(self, operations: list[EditOperation]) -> ProposalFlags:
        """Flags for every unverifiable control the operations touch (never blocks)."""
        named = list(
            dict.fromkeys(
                a for op in operations for a in (*existing_activities(op), *new_activities(op))
            )
        )
        flags: list[str] = []
        touched: list[str] = []
        for control in self.unverifiable_controls.values():
            hits = [a for a in named if control.matches(a)]
            if hits:
                flags.append(control.flag)
                touched += hits
        return ProposalFlags(
            flags=list(dict.fromkeys(flags)), activities=list(dict.fromkeys(touched))
        )
