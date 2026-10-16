"""Graph assembly, checkpointing and run logging.

WHY a checkpointer is mandatory here: ``human_review`` uses LangGraph's
``interrupt()``, which can only resume if the state was persisted. The analyst
may approve hours later, possibly from the Streamlit app rather than the
notebook that started the run.

Every run writes inputs, prompts, raw model outputs, model id, library versions
and timestamps to ``outputs/runs/<run_id>/`` -- reproducibility is a thesis
requirement, not a nicety.

Implemented in Phase 3.
"""
