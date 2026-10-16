"""Shared pytest fixtures.

WHY a hand-built synthetic log is the primary test fixture: on BPI 2019 nobody
knows the correct answer, so a passing assertion only proves the code ran. The
synthetic log (Phase 1) is small enough that the expected bottleneck, the
expected rework count and the expected conformance violations can be worked out
by hand and asserted exactly.

BPI 2019 is used only in tests marked ``@pytest.mark.bpi2019``, which are
excluded from the default run because they need a 695 MB file outside the repo.
"""
