"""The BPMN viewer as a Streamlit call: ``bpmn_viewer(xml, height)``.

The component lives in ``components/bpmn_viewer`` (vendored bpmn-js, served
by Streamlit from that folder). It only displays; it returns nothing.
"""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "bpmn_viewer", path=str(Path(__file__).parent / "components" / "bpmn_viewer")
)


def bpmn_viewer(xml: str, height: int, key: str) -> None:
    """Render BPMN 2.0 XML read-only, fitted to the frame, with zoom and pan."""
    _component(xml=xml, height=height, key=key, default=None)
