import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job, type UiSettings } from "./api";

const FINISHED = new Set(["succeeded", "failed", "interrupted"]);
// Used only until /settings/ui answers (configs/app.yaml holds the real values).
const FALLBACK_SETTINGS: UiSettings = { poll_seconds: 1, bpmn_height_px: 420 };

export function useSettings(): UiSettings {
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.uiSettings, staleTime: Infinity });
  return data ?? FALLBACK_SETTINGS;
}

/** Poll a background job until it ends; `onDone` runs once with the final job. */
export function useJob(jobId: string | null, onDone?: (job: Job) => void): Job | undefined {
  const settings = useSettings();
  const client = useQueryClient();
  const { data } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (q) =>
      q.state.data && FINISHED.has(q.state.data.status) ? false : settings.poll_seconds * 1000,
  });
  const [reported, setReported] = useState<string | null>(null);
  useEffect(() => {
    if (data && FINISHED.has(data.status) && reported !== data.id) {
      setReported(data.id);
      // Anything a job can change (datasets, runs, agent runs, health) is refetched.
      void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "job" });
      onDone?.(data);
    }
  }, [data, reported, client, onDone]);
  return data;
}

export const isRunning = (job: Job | undefined) => job !== undefined && !FINISHED.has(job.status);

// The last run and segment the analyst looked at, so Evidence and Redesign open
// where they left off. Only ids are stored; every page checks them against the API.
const SCOPE_KEY = "pp-scope";

export interface Scope {
  runId?: string;
  slug?: string;
}

export function readScope(): Scope {
  try {
    return JSON.parse(localStorage.getItem(SCOPE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function writeScope(scope: Scope): void {
  try {
    localStorage.setItem(SCOPE_KEY, JSON.stringify(scope));
    window.dispatchEvent(new Event("pp-scope"));
  } catch {
    /* storage blocked: the URL still carries the scope */
  }
}

export function useStoredScope(): Scope {
  const [scope, setScope] = useState(readScope);
  useEffect(() => {
    const update = () => setScope(readScope());
    window.addEventListener("pp-scope", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("pp-scope", update);
      window.removeEventListener("storage", update);
    };
  }, []);
  return scope;
}

export function usePersisted(key: string, initial: string): [string, (v: string) => void] {
  const [value, setValue] = useState(() => {
    try {
      return localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = (v: string) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      /* not persisted; still works for this session */
    }
  };
  return [value, set];
}
