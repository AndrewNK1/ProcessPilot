"""Graph nodes.

WHY the evidence-ID validator sits in the node and not in the prompt: prompts
are advisory, validators are not. A ``Diagnosis`` or ``RedesignProposal``
citing an ID absent from the store is rejected and the node is retried (max 2),
after which the run fails loudly rather than reporting an ungrounded finding.

Nodes (Phase 3): ``load_evidence``, ``diagnose``, ``redesign``, ``apply_edits``,
``validate``, ``human_review`` (LangGraph ``interrupt()``), ``finalize``.
"""
