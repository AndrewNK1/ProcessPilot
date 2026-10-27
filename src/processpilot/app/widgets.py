"""ProcessPilot's own UI pieces, as Streamlit custom components v2 (CCv2).

WHY CCv2 and not HTML strings: a CCv2 component renders in the page itself
(a shadow root, no iframe) and Streamlit injects its theme as ``--st-*`` CSS
variables, re-running the component when the theme changes. Every colour
below is one of those variables, so light and dark mode need no second set
of styles and nothing here knows which mode is on.

Every number shown is formatted in Python from values the API computed; the
components only lay them out (no metric is computed in the browser). The
one exception is geometry: bar widths are proportions of numbers passed in.

Pieces:
- ``brand``: the mark and name at the top of the sidebar.
- ``theme_switch``: Light / Dark. Streamlit has no Python API to change the
  appearance, so the switch writes the choice where Streamlit's own settings
  menu keeps it (``localStorage``) and reloads; the current selections ride
  along as query parameters (``ui.restore_selection``) so nothing is lost.
- ``step_track``: where the analyst is in the method on the Data page.
- ``kpi_strip``: headline figures in one divided container.
- ``wait_ladder``: the signature element. One path's steps top to bottom,
  each step's mean wait a bar starting where the previous one ended, so the
  ladder ends at the path's mean throughput and the longest rung is seen at
  once. A proposal's marks (automated, removed, added) sit on the rungs.
- ``compare_bars``: as-is against the ESTIMATE, drawn on one scale.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import streamlit as st

# Shared by every component: text in full ink, secondary text mixed from ink and
# ground (about 6:1 light, 8:1 dark), the data hue from blueColor (set to the
# teal in .streamlit/config.toml: deeper on light, brighter on dark).
_BASE_CSS = """
:host {
  --pp-ink: var(--st-text-color);
  --pp-muted: color-mix(in srgb, var(--st-text-color) 70%, var(--st-background-color));
  --pp-faint: color-mix(in srgb, var(--st-text-color) 12%, var(--st-background-color));
  --pp-rule: color-mix(in srgb, var(--st-text-color) 10%, var(--st-background-color));
  --pp-data: var(--st-blue-color);
  --pp-data-soft: color-mix(in srgb, var(--st-blue-color) 16%, var(--st-background-color));
  --pp-good: var(--st-green-text-color, var(--st-green-color));
  --pp-bad: var(--st-red-text-color, var(--st-red-color));
  font-family: var(--st-font);
  color: var(--pp-ink);
  font-size: 14px;
  line-height: 20px;
}
* { box-sizing: border-box; }
.num { font-variant-numeric: tabular-nums; }
.mono { font-family: var(--st-code-font); font-size: 12px; }
.muted { color: var(--pp-muted); }
"""

# --- brand ------------------------------------------------------------------------

_BRAND = st.components.v2.component(
    "pp_brand",
    html="""
<div class="brand">
  <svg viewBox="0 0 28 28" width="28" height="28" aria-hidden="true">
    <rect x="1" y="1" width="26" height="26" rx="7" class="tile"/>
    <path d="M7 9 H12 V14 H17 V19 H21" class="path"/>
    <circle cx="7" cy="9" r="2" class="node"/>
    <circle cx="21" cy="19" r="2" class="node"/>
  </svg>
  <div>
    <div class="name">ProcessPilot</div>
    <div class="tag muted">Evidence-grounded redesign</div>
  </div>
</div>
""",
    css=_BASE_CSS
    + """
.brand { display: flex; gap: 10px; align-items: center; padding: 2px 0 6px; }
.tile { fill: var(--pp-data-soft); }
.path { fill: none; stroke: var(--pp-data); stroke-width: 2.2; stroke-linecap: round;
        stroke-linejoin: round; }
.node { fill: var(--pp-data); }
.name { font-weight: 600; font-size: 16px; line-height: 20px; letter-spacing: -0.01em; }
.tag { font-size: 12px; line-height: 16px; }
""",
    js="""
export default function () {}
""",
)


def brand() -> None:
    """The product's mark and name (sidebar top)."""
    _BRAND(key="pp-brand")


# --- theme switch -----------------------------------------------------------------

_THEME_SWITCH = st.components.v2.component(
    "pp_theme_switch",
    html="""
<div class="switch" role="group" aria-label="Appearance">
  <button type="button" data-mode="light">
    <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.4"/>
      <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4
               M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/></svg>
    Light
  </button>
  <button type="button" data-mode="dark">
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M15.5 12.2A6 6 0 0 1 7.8 4.5a6 6 0 1 0 7.7 7.7z"/></svg>
    Dark
  </button>
</div>
""",
    css=_BASE_CSS
    + """
.switch { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; padding: 2px;
  background: var(--st-secondary-background-color); border: 1px solid var(--st-border-color);
  border-radius: 8px; }
button { all: unset; display: flex; align-items: center; justify-content: center; gap: 6px;
  height: 30px; border-radius: 6px; font: 500 13px/1 var(--st-font); color: var(--pp-muted);
  cursor: pointer; transition: background-color 120ms ease-out, color 120ms ease-out; }
button:hover { color: var(--pp-ink); }
button[aria-pressed="true"] { background: var(--st-background-color); color: var(--pp-ink);
  box-shadow: 0 0 0 1px var(--st-border-color); cursor: default; }
button:focus-visible { outline: 2px solid var(--st-primary-color); outline-offset: 1px; }
svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.5;
  stroke-linecap: round; }
button[data-mode="dark"] svg path { fill: none; }
""",
    js="""
function luminance(color) {
  const probe = document.createElement("canvas").getContext("2d");
  probe.fillStyle = color;
  const hex = probe.fillStyle;
  if (!hex.startsWith("#")) return 1;
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export default function (component) {
  const { data, parentElement } = component;
  const host = parentElement.host ?? parentElement;
  // The ground's own lightness decides which mode is on: Streamlit's server-side
  // st.context.theme can lag one rerun behind a change, the CSS never does.
  const ground = getComputedStyle(host).getPropertyValue("--st-background-color").trim();
  const current = luminance(ground) < 0.5 ? "dark" : "light";
  parentElement.querySelectorAll("button").forEach((button) => {
    const mode = button.dataset.mode;
    button.setAttribute("aria-pressed", String(mode === current));
    button.onclick = () => {
      if (mode === current) return;
      const value = JSON.stringify(mode === "dark" ? "Dark" : "Light");
      const paths = new Set([...(data?.paths ?? []), window.location.pathname]);
      try {
        // Streamlit's settings menu keeps the choice per page path, versioned.
        paths.forEach((p) => window.localStorage.setItem(`stActiveTheme-${p}-v2`, value));
      } catch (err) {
        return; // storage blocked: nothing to switch with, so leave the page as it is
      }
      const query = new URLSearchParams(data?.restore ?? {}).toString();
      window.location.replace(window.location.pathname + (query ? `?${query}` : ""));
    };
  });
}
""",
)


def theme_switch(paths: Sequence[str], restore: Mapping[str, str]) -> None:
    """Light / Dark switch; ``restore`` becomes the reloaded page's query string."""
    _THEME_SWITCH(key="pp-theme-switch", data={"paths": list(paths), "restore": dict(restore)})


# --- step track -------------------------------------------------------------------

StepState = Literal["done", "current", "todo"]

_STEP_TRACK = st.components.v2.component(
    "pp_step_track",
    html="""<ol class="track"></ol>""",
    css=_BASE_CSS
    + """
.track { list-style: none; margin: 0; padding: 0; display: grid;
  grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 0; }
li { position: relative; padding: 12px 12px 0 0; min-width: 0; }
li::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: var(--pp-rule); }
li.done::before { background: var(--pp-data); }
li.current::before { background: var(--pp-ink); }
li + li::after { content: ""; position: absolute; top: 0; left: -2px; width: 2px; height: 3px;
  background: var(--st-background-color); }
.label { display: flex; align-items: center; gap: 6px; font-weight: 500; }
li.todo .label { color: var(--pp-muted); font-weight: 400; }
.check { width: 14px; height: 14px; flex: none; stroke: var(--pp-data); stroke-width: 2;
  fill: none; stroke-linecap: round; stroke-linejoin: round; }
.detail { font-size: 12px; line-height: 16px; color: var(--pp-muted); margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 640px) {
  .track { grid-template-columns: 1fr; gap: 10px; }
  li { padding: 0 0 0 14px; }
  li::before { top: 0; bottom: 0; right: auto; width: 3px; height: auto; }
  li + li::after { display: none; }
}
""",
    js="""
export default function (component) {
  const { data, parentElement } = component;
  const list = parentElement.querySelector(".track");
  const steps = data?.steps ?? [];
  list.style.setProperty("--n", String(Math.max(steps.length, 1)));
  list.replaceChildren(...steps.map((step) => {
    const item = document.createElement("li");
    item.className = step.state;
    if (step.state === "current") item.setAttribute("aria-current", "step");
    const label = document.createElement("div");
    label.className = "label";
    if (step.state === "done") {
      label.insertAdjacentHTML("beforeend", '<svg class="check" viewBox="0 0 14 14" '
        + 'aria-hidden="true"><path d="M2.5 7.5l3 3 6-7"/></svg>');
    }
    label.append(step.label);
    const detail = document.createElement("div");
    detail.className = "detail";
    detail.textContent = step.detail || (step.state === "todo" ? "Not started" : "");
    item.append(label, detail);
    const said = { done: "done", current: "current step", todo: "not started" };
    item.setAttribute("aria-label", `${step.label}: ${said[step.state] ?? step.state}`);
    return item;
  }));
}
""",
)


def step_track(steps: Sequence[tuple[str, StepState, str]]) -> None:
    """``(label, state, detail)`` per step of the method, in order."""
    _STEP_TRACK(
        key="pp-step-track",
        data={"steps": [{"label": a, "state": s, "detail": d} for a, s, d in steps]},
    )


# --- KPI strip --------------------------------------------------------------------

_KPI_STRIP = st.components.v2.component(
    "pp_kpi_strip",
    html="""<dl class="strip"></dl>""",
    css=_BASE_CSS
    + """
.strip { margin: 0; display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr));
  border: 1px solid var(--st-border-color); border-radius: 8px;
  background: var(--st-background-color); }
.cell { padding: 14px 18px 14px; min-width: 0; display: flex; flex-direction: column; }
dt { flex: 1; }
.cell + .cell { border-left: 1px solid var(--pp-rule); }
dt { font-size: 13px; line-height: 18px; color: var(--pp-muted); }
dd { margin: 4px 0 0; font-size: 24px; line-height: 30px; font-weight: 500;
  font-variant-numeric: tabular-nums; letter-spacing: -0.01em; white-space: nowrap; }
.unit { font-size: 14px; color: var(--pp-muted); font-weight: 400; margin-left: 3px;
  letter-spacing: 0; }
.note { font-size: 12px; line-height: 16px; color: var(--pp-muted); margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 720px) {
  .strip { grid-template-columns: 1fr 1fr; }
  .cell + .cell { border-left: 0; }
  .cell { border-top: 1px solid var(--pp-rule); }
  .cell:nth-child(-n+2) { border-top: 0; }
  .cell:nth-child(even) { border-left: 1px solid var(--pp-rule); }
  .cell:last-child:nth-child(odd) { grid-column: 1 / -1; }
}
""",
    js="""
export default function (component) {
  const { data, parentElement } = component;
  const strip = parentElement.querySelector(".strip");
  const items = data?.items ?? [];
  strip.style.setProperty("--n", String(Math.max(items.length, 1)));
  strip.replaceChildren(...items.map((it) => {
    const cell = document.createElement("div");
    cell.className = "cell";
    const label = document.createElement("dt");
    label.textContent = it.label;
    const value = document.createElement("dd");
    value.textContent = it.value;
    if (it.unit) {
      const unit = document.createElement("span");
      unit.className = "unit";
      unit.textContent = it.unit;
      value.append(unit);
    }
    cell.append(label, value);
    if (it.note) {
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = it.note;
      note.title = it.note;
      cell.append(note);
    }
    return cell;
  }));
}
""",
)


def kpi_strip(items: Sequence[Mapping[str, str | None]], key: str) -> None:
    """Items with ``label``, ``value``, optional ``unit`` and ``note`` (all preformatted)."""
    _KPI_STRIP(key=key, data={"items": [dict(i) for i in items]})


# --- wait ladder ------------------------------------------------------------------

_WAIT_LADDER = st.components.v2.component(
    "pp_wait_ladder",
    html="""<figure class="ladder"><div class="axis"></div><div class="rows"></div>
<figcaption class="caption"></figcaption></figure>""",
    css=_BASE_CSS
    + """
.ladder { margin: 0; --name: minmax(160px, 30%); --value: 92px; }
.axis, .row { display: grid; grid-template-columns: var(--name) 1fr var(--value);
  column-gap: 16px; align-items: center; }
.axis { height: 22px; font-size: 12px; color: var(--pp-muted); }
.ticks { position: relative; height: 22px; }
.tick { position: absolute; top: 3px; transform: translateX(-50%); white-space: nowrap; }
.tick:first-child { transform: none; }
.tick.last { transform: translateX(-100%); }
.rows { position: relative; }
.row { height: 40px; }
.name { min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.activity { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { display: flex; gap: 6px; align-items: center; font-size: 12px; line-height: 16px;
  color: var(--pp-muted); min-width: 0; }
.id { font-family: var(--st-code-font); font-size: 11.5px; color: var(--pp-data);
  white-space: nowrap; }
.track { position: relative; height: 40px; }
.grid { position: absolute; top: -22px; bottom: 0; width: 0;
  border-left: 1px dashed var(--pp-rule); }
.bar { position: absolute; top: 14px; height: 12px; min-width: 2px; border-radius: 2px;
  background: var(--pp-data); }
.riser { position: absolute; top: -14px; height: 28px; width: 1px; background: var(--pp-muted); }
.start { position: absolute; top: 15px; width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid var(--pp-data); background: var(--st-background-color);
  transform: translateX(-1px); }
.bar.automated { background: repeating-linear-gradient(135deg, var(--pp-data) 0 3px,
  var(--pp-data-soft) 3px 6px); }
.bar.removed { background: transparent; border: 1.5px dashed var(--pp-muted); }
.row.removed .activity { text-decoration: line-through; color: var(--pp-muted); }
.row.added .activity { font-style: normal; }
.bar.added { background: transparent; border: 1.5px dashed var(--pp-data); }
.tag { font-size: 11px; line-height: 16px; padding: 0 6px; border-radius: 999px;
  background: var(--pp-data-soft); color: var(--pp-ink); white-space: nowrap; flex: none; }
.value { text-align: right; font-variant-numeric: tabular-nums; line-height: 17px; }
.value .sub { font-size: 12px; color: var(--pp-muted); }
.row.top .value .main { font-weight: 600; }
.caption { margin-top: 10px; font-size: 12px; line-height: 17px; color: var(--pp-muted);
  max-width: 78ch; }
.empty { padding: 14px 0; color: var(--pp-muted); }
@media (max-width: 640px) {
  .ladder { --name: 1fr; --value: 96px; }
  .axis { display: none; }
  .row { grid-template-columns: 1fr var(--value); grid-template-rows: auto 18px; height: auto;
    padding: 6px 0; row-gap: 2px; }
  .name { grid-column: 1; grid-row: 1; }
  .value { grid-column: 2; grid-row: 1 / span 2; }
  .track { grid-column: 1; grid-row: 2; height: 18px; }
  .bar { top: 3px; } .start { top: 4px; } .riser, .grid { display: none; }
}
""",
    js="""
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function niceStep(max, target) {
  const raw = max / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

export default function (component) {
  const { data, parentElement } = component;
  const rowsEl = parentElement.querySelector(".rows");
  const axisEl = parentElement.querySelector(".axis");
  const caption = parentElement.querySelector(".caption");
  const steps = data?.steps ?? [];
  caption.textContent = data?.caption ?? "";
  if (!steps.length || !data.total) {
    axisEl.replaceChildren();
    rowsEl.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = data?.empty ?? "No cases follow this path.";
    rowsEl.append(empty);
    return;
  }
  // Axis in the unit that keeps tick labels short; ticks are round and end above the total.
  const unit = data.total >= 2 * 86400 ? [86400, "d"]
    : data.total >= 2 * 3600 ? [3600, "h"] : [60, "min"];
  const totalUnits = data.total / unit[0];
  const step = niceStep(totalUnits, 4);
  const top = Math.ceil(totalUnits / step) * step;
  const scale = (seconds) => (100 * seconds) / (top * unit[0]);
  const ticks = [];
  for (let t = 0; t <= top + 1e-9; t += step) ticks.push(t);
  const tickRow = document.createElement("div");
  tickRow.className = "ticks";
  ticks.forEach((t, i) => {
    const el = document.createElement("span");
    el.className = "tick" + (i === ticks.length - 1 ? " last" : "");
    el.style.left = `${(100 * t) / top}%`;
    el.textContent = `${+t.toFixed(2)} ${unit[1]}`;
    tickRow.append(el);
  });
  axisEl.replaceChildren(document.createElement("span"), tickRow, document.createElement("span"));

  let cursor = 0;
  const longest = Math.max(...steps.map((s) => s.seconds ?? 0));
  rowsEl.replaceChildren(...steps.map((s, i) => {
    const row = document.createElement("div");
    row.className = "row" + (s.mark ? ` ${s.mark}` : "")
      + (s.seconds && s.seconds === longest ? " top" : "");
    const name = document.createElement("div");
    name.className = "name";
    const activity = document.createElement("span");
    activity.className = "activity";
    activity.textContent = s.activity;
    activity.title = s.activity;
    name.append(activity);
    if (s.evidence_id || s.tag || s.meta) {
      const meta = document.createElement("span");
      meta.className = "meta";
      if (s.tag) meta.append(el("span", "tag", s.tag));
      if (s.evidence_id) meta.append(el("span", "id", s.evidence_id));
      if (s.meta) meta.append(el("span", "", s.meta));
      name.append(meta);
    }
    const track = document.createElement("div");
    track.className = "track";
    if (i === 0) ticks.forEach((t) => {
      const g = document.createElement("span");
      g.className = "grid";
      g.style.left = `${(100 * t) / top}%`;
      g.style.height = `calc(${steps.length * 100}% + 22px)`;
      track.append(g);
    });
    if (s.seconds == null) {
      if (i === 0 || s.start) {
        const dot = document.createElement("span");
        dot.className = "start";
        dot.style.left = `${scale(cursor)}%`;
        track.append(dot);
      }
    } else {
      if (i > 0) {
        const riser = document.createElement("span");
        riser.className = "riser";
        riser.style.left = `${scale(cursor)}%`;
        track.append(riser);
      }
      const bar = document.createElement("span");
      bar.className = "bar" + (s.mark ? ` ${s.mark}` : "");
      bar.style.left = `${scale(cursor)}%`;
      bar.style.width = `${scale(s.seconds)}%`;
      bar.title = s.hover ?? "";
      track.append(bar);
      cursor += s.seconds;
    }
    const value = document.createElement("div");
    value.className = "value";
    value.innerHTML = "";
    const main = document.createElement("div");
    main.className = "main";
    main.textContent = s.value ?? "";
    value.append(main);
    if (s.sub) value.append(el("div", "sub", s.sub));
    row.append(name, track, value);
    return row;
  }));
}
""",
)


def wait_ladder(
    steps: Sequence[Mapping[str, Any]],
    total_seconds: float | None,
    caption: str,
    key: str,
    empty: str = "No cases follow this path.",
) -> None:
    """One path as a wait ladder.

    Each step: ``activity``, ``seconds`` (bar length; ``None`` for the start or a
    step with nothing to draw), ``value`` / ``sub`` (preformatted right column),
    optional ``evidence_id``, ``tag``, ``meta``, ``hover`` and ``mark``
    (``automated``, ``removed`` or ``added``).
    """
    _WAIT_LADDER(
        key=key,
        data={
            "steps": [dict(s) for s in steps],
            "total": total_seconds,
            "caption": caption,
            "empty": empty,
        },
    )


# --- as-is vs ESTIMATE ------------------------------------------------------------

_COMPARE = st.components.v2.component(
    "pp_compare_bars",
    html="""<div class="compare"></div>""",
    css=_BASE_CSS
    + """
.compare { display: grid; grid-template-columns: 72px 1fr auto; column-gap: 12px;
  row-gap: 6px; align-items: center; }
.head { grid-column: 1 / -1; font-size: 13px; color: var(--pp-muted); margin-top: 8px; }
.head:first-child { margin-top: 0; }
.label { font-size: 12px; color: var(--pp-muted); white-space: nowrap; }
.label.estimate { color: var(--pp-ink); font-weight: 500; letter-spacing: 0.02em; }
.track { position: relative; height: 12px; }
.bar { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px;
  background: color-mix(in srgb, var(--st-text-color) 38%, var(--st-background-color)); }
.bar.estimate { background: var(--pp-data-soft); border: 1.5px dashed var(--pp-data); }
.value { text-align: right; font-variant-numeric: tabular-nums; min-width: 56px; }
.delta { grid-column: 2 / -1; font-size: 12px; line-height: 16px; text-align: right;
  font-variant-numeric: tabular-nums; color: var(--pp-muted); margin-top: -2px; }
.delta.good { color: var(--pp-good); }
.delta.bad { color: var(--pp-bad); }
""",
    js="""
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export default function (component) {
  const { data, parentElement } = component;
  const root = parentElement.querySelector(".compare");
  const nodes = [];
  (data?.rows ?? []).forEach((r) => {
    // Each pair on its own scale: today's value and its ESTIMATE are what is compared.
    const max = Math.max(r.now ?? 0, r.estimate ?? 0, 1);
    nodes.push(el("div", "head", r.label));
    [["now", "Today", r.now, r.now_text], ["estimate", "ESTIMATE", r.estimate, r.estimate_text]]
      .forEach(([kind, name, seconds, text]) => {
        const track = el("div", "track");
        const bar = el("span", `bar ${kind}`);
        bar.style.width = `${(100 * (seconds ?? 0)) / max}%`;
        bar.setAttribute("role", "img");
        bar.setAttribute("aria-label", `${r.label}, ${name.toLowerCase()}: ${text}`);
        track.append(bar);
        nodes.push(el("div", `label ${kind}`, name), track, el("div", "value", text));
      });
    if (r.delta) nodes.push(el("span", ""), el("div", `delta ${r.direction ?? ""}`, r.delta));
  });
  root.replaceChildren(...nodes);
}
""",
)


def compare_bars(rows: Sequence[Mapping[str, Any]], key: str) -> None:
    """Rows of ``label``, ``now`` / ``estimate`` seconds, their texts, ``delta``, ``direction``."""
    _COMPARE(key=key, data={"rows": [dict(r) for r in rows]})
