// Number formatting shared by every screen. Ported from the Streamlit
// helpers so figures read the same as in notebooks and evidence summaries.

const UNITS: [string, number][] = [
  ["d", 86400],
  ["h", 3600],
  ["min", 60],
];

/** `[value, unit]` for a duration, e.g. `["3.5", "d"]`; `["—", ""]` when missing. */
export function durationParts(seconds: number | null | undefined): [string, string] {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return ["—", ""];
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) {
      const value = seconds / size;
      return [unit === "min" ? value.toFixed(0) : value.toFixed(1), unit];
    }
  }
  return [seconds.toFixed(0), "s"];
}

/** `3.5 d`, `4.0 h`, `12 min`, `40 s`; `—` for missing values. */
export function fmtDuration(seconds: number | null | undefined): string {
  const [value, unit] = durationParts(seconds);
  return `${value} ${unit}`.trim();
}

/** `0.123` -> `12.3%`; `—` when missing. */
export function fmtShare(share: number | null | undefined, digits = 1): string {
  return share === null || share === undefined ? "—" : `${(share * 100).toFixed(digits)}%`;
}

/** Thousands separators, no decimals. */
export function fmtInt(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : Math.round(n).toLocaleString("en-US");
}

/** `2026-10-27 11:39` from an ISO timestamp (shown in UTC, as stored). */
export function fmtStamp(iso: string): string {
  return iso.replace("T", " ").slice(0, 16);
}

/** A short evidence ID for tags: `BN-01-2-way-match` -> `BN-01` (the segment is on screen). */
export function shortId(id: string): string {
  const match = /^([A-Z]+-(?:R-)?\d+)/.exec(id);
  return match ? match[1] : id;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}
