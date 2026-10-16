"""As-is process discovery: BPMN model, DFG, and performance DFG.

WHY the inductive miner: it is the only mainstream discovery algorithm that
guarantees a sound model, and ``apply_edits`` later needs a structurally valid
BPMN to transform. The noise threshold is configurable because real logs like
BPI 2019 produce an unreadable flower model at threshold 0.

The installed pm4py version's API must be checked before use -- pm4py renames
discovery entry points between minor versions.

Implemented in Phase 2.
"""
