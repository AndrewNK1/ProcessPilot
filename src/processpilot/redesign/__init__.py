"""Redesign: typed edit operations, deterministic BPMN editing, impact estimate.

The agent proposes operations; this package is the only thing that executes
them, in code, with no model in the loop. That is what keeps "the system never
executes changes" and "the model never produces a number" both true.
"""
