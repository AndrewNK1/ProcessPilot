"""Applies edit operations to the as-is BPMN, producing ``to_be.bpmn``.

WHY it fails loudly: a partially applied edit would yield a to-be model that
does not correspond to the proposal the analyst approved. Any invalid or
unapplicable operation raises; there is no best-effort mode.

Also the enforcement point for ``protected_activities`` -- the constraint is
injected into the redesign prompt, but a prompt cannot be relied upon, so the
machine-readable subset is checked here too.

Implemented in Phase 4.
"""
