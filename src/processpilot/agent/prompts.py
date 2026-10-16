"""Prompt templates, versioned.

WHY prompts live in code and are logged per run: a thesis result that cannot be
reproduced is not a result. Each template carries a version string that is
written into the run log alongside the model id.

Prompts render the *evidence items*, the heuristics catalogue and the analyst's
constraints -- never raw event data.

Implemented in Phase 3.
"""
