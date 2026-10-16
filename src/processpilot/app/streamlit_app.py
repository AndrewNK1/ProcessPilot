"""Streamlit front end for business analysts.

WHY a UI is part of the thesis and not decoration: the claim is that an SME
business analyst -- not a data scientist -- can run this. The mapping
confirmation screen, the evidence table, and the approve/reject control are
where that claim is either true or false.

Screens (Phase 5): upload / connect source -> confirm suggested mapping ->
data-quality report -> per-segment evidence -> proposals with cited evidence ->
as-is vs to-be BPMN side by side (bpmn-js via components.v1) -> approve/reject
with a comment, which resumes the interrupted graph.
"""
