"""Replay-based impact ESTIMATE. Not a simulation of reality.

WHY an estimate and not a discrete-event simulation: a DES would need arrival
rates, resource capacities and service-time distributions that the log does not
contain, and inventing them would make the result look more authoritative than
it is. Instead the historical traces are replayed under the edit:

    - removed activities/edges: their observed waiting time drops out
    - parallelised segments: cost becomes max() of the branches, not sum()

Output: projected change in median throughput time, plus an explicit list of
assumptions. Every caller and every UI surface must label this an ESTIMATE.

Implemented in Phase 4.
"""
