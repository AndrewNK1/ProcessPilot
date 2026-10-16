"""The agent: a fixed LangGraph pipeline, not a free-roaming agent.

Node order per segment:
    load_evidence -> diagnose -> redesign -> apply_edits -> validate
                  -> human_review (interrupt) -> finalize | back to redesign

``load_evidence``, ``apply_edits`` and ``validate`` contain no LLM call.
``diagnose`` and ``redesign`` contain nothing but an LLM call with structured
output. Keeping those sets disjoint is what makes the system auditable.
"""
