"""Environment sanity checks.

WHY these are tests and not a one-off script: the install pulled pandas 3.0 and
numpy 2.4, which are new enough that pm4py interoperability is not safe to
assume. If a future `pip install -U` breaks the pm4py/pandas boundary, every
mined number silently changes. This test fails loudly instead.

It also pins the pm4py entry points the project depends on, so an API rename in
a later pm4py release surfaces here rather than mid-pipeline.
"""

import pandas as pd
import pm4py
import pytest

# Entry points the mining and redesign layers call. Checked by name because
# pm4py renames discovery functions between minor versions.
REQUIRED_PM4PY_API = [
    "read_xes",
    "format_dataframe",
    "discover_dfg",
    "discover_performance_dfg",
    "discover_petri_net_inductive",
    "discover_bpmn_inductive",
    "convert_to_bpmn",
    "write_bpmn",
    "read_bpmn",
    "get_variants",
    "get_event_attribute_values",
]


@pytest.mark.parametrize("name", REQUIRED_PM4PY_API)
def test_pm4py_exposes_required_api(name: str) -> None:
    assert hasattr(pm4py, name), f"installed pm4py {pm4py.__version__} has no {name}"


@pytest.fixture
def tiny_pm4py_log() -> pd.DataFrame:
    """Three-activity log in pm4py's native column names, known DFG by hand."""
    df = pd.DataFrame(
        {
            "case:concept:name": ["c1", "c1", "c1", "c2", "c2"],
            "concept:name": ["A", "B", "C", "A", "C"],
            "time:timestamp": pd.to_datetime(
                [
                    "2024-01-01 09:00",
                    "2024-01-01 10:00",
                    "2024-01-02 09:00",
                    "2024-01-01 09:30",
                    "2024-01-01 11:30",
                ]
            ),
        }
    )
    return pm4py.format_dataframe(
        df,
        case_id="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )


def test_pm4py_accepts_pandas_3_dataframe(tiny_pm4py_log: pd.DataFrame) -> None:
    """The pandas 3.0 / pm4py boundary holds for the hand-computed DFG."""
    dfg, _start, _end = pm4py.discover_dfg(tiny_pm4py_log)
    assert dfg == {("A", "B"): 1, ("B", "C"): 1, ("A", "C"): 1}


def test_inductive_miner_yields_convertible_bpmn(tiny_pm4py_log: pd.DataFrame) -> None:
    """Discovery must produce a BPMN, since redesign edits BPMN rather than a Petri net."""
    net, im, fm = pm4py.discover_petri_net_inductive(tiny_pm4py_log)
    bpmn = pm4py.convert_to_bpmn(net, im, fm)
    assert len(bpmn.get_nodes()) > 0


def test_langchain_and_langgraph_entry_points_importable() -> None:
    """Pinned here because langchain 1.x moved init_chat_model out of the top level."""
    from langchain.chat_models import init_chat_model
    from langgraph.graph import StateGraph
    from langgraph.types import interrupt

    assert callable(init_chat_model)
    assert callable(interrupt)
    assert StateGraph is not None
