import { useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Database, GitPullRequestArrow, Menu, Monitor, Moon, ScanSearch, Sun, X } from "lucide-react";
import { api } from "./api";
import { useStoredScope, usePersisted } from "./hooks";
import { useScreenEnter } from "./motion";
import { Seg } from "./ui";
import { DataPage } from "./pages/DataPage";
import { EvidencePage } from "./pages/EvidencePage";
import { RedesignPage } from "./pages/RedesignPage";

export function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  const where = location.pathname.split("/")[1] || "data";
  const canvas = useRef<HTMLElement>(null);
  useScreenEnter(canvas, location.pathname);

  return (
    <div className="app">
      <div className="topbar">
        <button className="btn ghost" type="button" aria-label="Open the menu" onClick={() => setMenuOpen(true)}>
          <Menu size={20} />
        </button>
        <Mark size={20} />
        <span className="where">{where[0].toUpperCase() + where.slice(1)}</span>
      </div>
      {menuOpen && <div className="sheet-backdrop" onClick={() => setMenuOpen(false)} aria-hidden />}
      <Shell open={menuOpen} onClose={() => setMenuOpen(false)} />
      <main className="canvas" id="main" ref={canvas}>
        <Routes>
          <Route path="/" element={<Navigate to="/data" replace />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/evidence/:runId?/:slug?" element={<EvidencePage />} />
          <Route path="/redesign/:runId?/:slug?" element={<RedesignPage />} />
          <Route path="*" element={<Navigate to="/data" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export function Mark({ size = 22 }: { size?: number }) {
  // A ticket with its notches: the product's mark (drawn for ProcessPilot, replaceable).
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <path fill="var(--mark)" d="M3 7h26v6a3 3 0 0 0 0 6v6H3v-6a3 3 0 0 0 0-6z" />
      <path fill="var(--carbon-soft)" d="M10 12h12v2H10zm0 6h8v2h-8z" />
    </svg>
  );
}

function useNarrow(): boolean {
  const query = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}

function Shell({ open, onClose }: { open: boolean; onClose: () => void }) {
  // On a phone the shell is an off-screen sheet: closed, it must not take keyboard focus.
  const hidden = useNarrow() && !open;
  const scope = useStoredScope();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const run = useQuery({
    queryKey: ["run", scope.runId],
    queryFn: () => api.run(scope.runId as string),
    enabled: !!scope.runId,
  });
  const agentRuns = useQuery({
    queryKey: ["agent-runs", scope.runId, scope.slug],
    queryFn: () => api.agentRuns(scope.runId as string, scope.slug as string),
    enabled: !!scope.runId && !!scope.slug,
  });
  const mined = (runs.data ?? []).filter((r) => r.status === "mined").length;
  const segment = run.data?.segments.find((s) => s.slug === scope.slug);
  const awaiting = (agentRuns.data ?? []).some((a) => a.status === "awaiting_review");
  const scopePath = scope.runId ? `/${scope.runId}${scope.slug ? `/${scope.slug}` : ""}` : "";

  return (
    <nav className={`shell${open ? " open" : ""}`} aria-label="ProcessPilot" inert={hidden}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <Link to="/data" className="brand">
          <Mark />
          ProcessPilot
        </Link>
        {open && (
          <button className="btn ghost" type="button" aria-label="Close the menu" onClick={onClose}>
            <X size={18} />
          </button>
        )}
      </div>
      <NavLink to="/data" className="leg">
        <Database size={16} aria-hidden />
        Data
        <span className="leg-state">{mined ? `${mined} ${mined === 1 ? "run" : "runs"}` : "start"}</span>
      </NavLink>
      <NavLink to={`/evidence${scopePath}`} className="leg">
        <ScanSearch size={16} aria-hidden />
        Evidence
        <span className="leg-state">{run.data ? `${run.data.segments.length} segments` : ""}</span>
      </NavLink>
      <NavLink to={`/redesign${scopePath}`} className="leg">
        <GitPullRequestArrow size={16} aria-hidden />
        Redesign
        <span className={`leg-state${awaiting ? " open" : ""}`}>{awaiting ? "Decide" : ""}</span>
      </NavLink>

      {scope.runId && (
        <>
          <div className="nav-label">Scope</div>
          <div className="scope">
            <strong>{run.data?.preset ?? (run.data ? "Preset not named" : "…")}</strong>
            <span className="num">Run {scope.runId}</span>
          </div>
          {segment && (
            <div className="scope">
              <strong>{segment.name}</strong>
              <span>
                {(run.data?.segments.findIndex((s) => s.slug === segment.slug) ?? 0) + 1} of{" "}
                {run.data?.segments.length} segments
              </span>
            </div>
          )}
        </>
      )}

      <div className="shell-foot">
        <ThemeSwitch />
        <div className="status-line">
          {health.isError ? (
            <span className="warn">API not reachable</span>
          ) : health.data ? (
            <>
              Model <b>{health.data.model_id ?? "not set (PP_MODEL)"}</b>
              {health.data.active_jobs > 0 && (
                <>
                  <br />
                  {health.data.active_jobs} background {health.data.active_jobs === 1 ? "job" : "jobs"} running
                </>
              )}
              {!health.data.graphviz && (
                <>
                  <br />
                  <span className="warn">Graphviz not on PATH: diagrams have no layout</span>
                </>
              )}
            </>
          ) : (
            "Connecting…"
          )}
        </div>
      </div>
    </nav>
  );
}

type Theme = "system" | "light" | "dark";

function ThemeSwitch() {
  const [theme, setTheme] = usePersisted("pp-theme", "system");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    else delete root.dataset.theme;
    window.dispatchEvent(new Event("pp-theme"));
  }, [theme]);
  return (
    <Seg<Theme>
      label="Appearance"
      value={theme as Theme}
      onChange={setTheme}
      options={[
        { id: "system", label: <Monitor size={14} aria-label="System" /> },
        { id: "light", label: <Sun size={14} aria-label="Light" /> },
        { id: "dark", label: <Moon size={14} aria-label="Dark" /> },
      ]}
    />
  );
}
