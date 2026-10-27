"""The BPMN viewer as a Streamlit call: ``bpmn_viewer(xml, height, key, highlight)``.

The component lives in ``components/bpmn_viewer`` (vendored bpmn-js, served
by Streamlit from that folder). It only displays; it returns nothing. It
follows the app's light or dark theme on its own (Streamlit sends the theme
with every render).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "bpmn_viewer", path=str(Path(__file__).parent / "components" / "bpmn_viewer")
)


def bpmn_viewer(xml: str, height: int, key: str, highlight: Sequence[str] = ()) -> None:
    """Render BPMN 2.0 XML read-only, fitted to the frame, with zoom and pan.

    ``highlight`` names tasks to outline in the theme's accent (what a proposal
    changes); names not in the model are ignored.
    """
    _component(xml=xml, height=height, highlight=list(highlight), key=key, default=None)
