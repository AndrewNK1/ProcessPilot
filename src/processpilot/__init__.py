"""ProcessPilot: an evidence-grounded AI consultant for BPM analysis and redesign.

The package is deliberately split so that the boundary between *computation* and
*reasoning* is a module boundary, not a convention:

- ``sources``, ``ingest``, ``mining``  -- deterministic. pandas/pm4py produce
  every number. No LLM call may occur here.
- ``agent``                            -- reasoning only. Reads precomputed
  evidence items through read-only tools; never touches the event log.
- ``redesign``                         -- deterministic again. Applies the
  agent's typed edit operations to BPMN and estimates impact.

Nothing in this package is specific to a dataset; dataset knowledge lives in
``configs/``.
"""

__version__ = "0.1.0"
