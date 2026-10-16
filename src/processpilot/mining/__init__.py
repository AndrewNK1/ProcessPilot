"""Mining engine: the deterministic half of ProcessPilot.

Every number the LLM ever reasons about is produced here by pandas or pm4py.
Functions in this package are pure: they take a dataframe and configuration and
return values. Writing files (BPMN, JSON) is the caller's job, so that the same
function can be unit-tested on a 10-case synthetic log with no filesystem.
"""
