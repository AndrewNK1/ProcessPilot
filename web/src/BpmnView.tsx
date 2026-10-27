// Read-only BPMN viewer (bpmn-js navigated viewer: scroll to zoom, drag to pan).
//
// The diagram takes the theme's ground and ink (bpmn-js renderer defaults) and
// is rebuilt when the appearance changes. Tasks named in `highlight` (what a
// proposal changes) are outlined in carbon. The bpmn.io watermark is part of
// bpmn-js's licence and must stay fully visible: it takes the ink colour so it
// never disappears on the dark ground, and it is never hidden or covered.

import { useEffect, useRef, useState } from "react";
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";

function themeColours(): { ground: string; ink: string } {
  const css = getComputedStyle(document.documentElement);
  return { ground: css.getPropertyValue("--ground").trim(), ink: css.getPropertyValue("--ink").trim() };
}

function useThemeKey(): string {
  const [key, setKey] = useState(() => JSON.stringify(themeColours()));
  useEffect(() => {
    const update = () => setKey(JSON.stringify(themeColours()));
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    window.addEventListener("pp-theme", update);
    media.addEventListener("change", update);
    return () => {
      window.removeEventListener("pp-theme", update);
      media.removeEventListener("change", update);
    };
  }, []);
  return key;
}

export function BpmnView({
  xml,
  height,
  highlight = [],
  label,
}: {
  xml: string | undefined;
  height: number;
  highlight?: string[];
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const themeKey = useThemeKey();
  const highlightKey = highlight.join("\u0000");

  useEffect(() => {
    if (!host.current || !xml) return;
    const { ground, ink } = JSON.parse(themeKey);
    const viewer = new NavigatedViewer({
      container: host.current,
      bpmnRenderer: { defaultFillColor: ground, defaultStrokeColor: ink, defaultLabelColor: ink },
    });
    let cancelled = false;
    viewer
      .importXML(xml)
      .then(() => {
        if (cancelled) return;
        const canvas = viewer.get("canvas") as any;
        const wanted = new Set(highlightKey ? highlightKey.split("\u0000") : []);
        (viewer.get("elementRegistry") as any)
          .filter((el: any) => el.businessObject && wanted.has(el.businessObject.name))
          .forEach((el: any) => canvas.addMarker(el.id, "pp-changed"));
        canvas.zoom("fit-viewport", "auto");
        setError(null);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      viewer.destroy();
    };
  }, [xml, themeKey, highlightKey]);

  return (
    <figure>
      <div className="bpmn" ref={host} style={{ height }} role="img" aria-label={label} />
      {error && <p className="small" style={{ color: "var(--signal)", marginTop: 8 }}>Could not display the model: {error}</p>}
      {!xml && !error && <p className="small muted" style={{ marginTop: 8 }}>Loading the model…</p>}
    </figure>
  );
}
