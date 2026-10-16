"""Resource workload and handover analysis.

WHY handovers get their own metric: waiting time concentrated at a handover
points at an organisational fix (numerical involvement, extra resources) while
the same waiting inside one resource's queue points at a capacity fix. The
heuristic chosen depends on which it is.

Skipped entirely, with an explicit note in the evidence store, when the log has
no resource column -- a silent skip would let the agent assume no resource
problem exists.

Implemented in Phase 2.
"""
