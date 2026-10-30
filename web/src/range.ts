/** Time range presets, rolling ends, prev/next, and URL persistence. */

export type TimeRange = { startMs: number; endMs: number };

export type RangeState = {
  preset: string;
  custom: TimeRange | null;
  label: string;
};

export const PRESETS: [string, string, number | null][] = [
  ["1h", "Last hour", 3600_000],
  ["6h", "Last 6 hours", 6 * 3600_000],
  ["today", "Today", null],
  ["24h", "Last 24 hours", 24 * 3600_000],
  ["7d", "Last 7 days", 7 * 86400_000],
  ["30d", "Last 30 days", 30 * 86400_000],
  ["all", "All", null],
];

export function rangeForPreset(preset: string, nowMs = Date.now()): TimeRange {
  const now = nowMs;
  if (preset === "all") return { startMs: now - 365 * 86400_000, endMs: now };
  if (preset === "today") {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return { startMs: d.getTime(), endMs: now };
  }
  const found = PRESETS.find((p) => p[0] === preset);
  const ms = found?.[2] ?? 24 * 3600_000;
  return { startMs: now - (ms || 24 * 3600_000), endMs: now };
}

export function resolveRange(state: RangeState, nowMs = Date.now()): TimeRange {
  if (state.custom) return { ...state.custom };
  return rangeForPreset(state.preset || "24h", nowMs);
}

export function isRollingPreset(preset: string, custom: TimeRange | null): boolean {
  return !custom && PRESETS.some((p) => p[0] === preset);
}

export function fmtRange(r: TimeRange, timeZone?: string): string {
  const opt: Intl.DateTimeFormatOptions = {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  };
  return new Date(r.startMs).toLocaleString(undefined, opt) + " to " + new Date(r.endMs).toLocaleString(undefined, opt);
}

export function shiftRange(range: TimeRange, direction: -1 | 1): TimeRange {
  const span = Math.max(1, range.endMs - range.startMs);
  return {
    startMs: range.startMs + direction * span,
    endMs: range.endMs + direction * span,
  };
}

export function customLabel(range: TimeRange): string {
  return fmtRange(range);
}

export type UrlViewerState = {
  project?: string;
  preset?: string;
  start?: string;
  end?: string;
  q?: string;
  live?: boolean;
  tz?: "local" | "utc";
};

export function readUrlState(search = typeof location !== "undefined" ? location.search : ""): UrlViewerState {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const start = params.get("start") || undefined;
  const end = params.get("end") || undefined;
  const live = params.get("live");
  return {
    project: params.get("project") || undefined,
    preset: params.get("preset") || undefined,
    start,
    end,
    q: params.get("q") || undefined,
    live: live == null ? undefined : live !== "0",
    tz: params.get("tz") === "utc" ? "utc" : params.get("tz") === "local" ? "local" : undefined,
  };
}

export function writeUrlState(state: UrlViewerState): void {
  if (typeof history === "undefined" || typeof location === "undefined") return;
  const params = new URLSearchParams(location.search);
  params.delete("t");
  params.delete("token");
  const setOrDel = (key: string, value: string | undefined) => {
    if (value == null || value === "") params.delete(key);
    else params.set(key, value);
  };
  const hasCustom = Boolean(state.start && state.end);
  setOrDel("project", state.project);
  setOrDel("preset", hasCustom ? undefined : state.preset);
  setOrDel("start", state.start);
  setOrDel("end", state.end);
  setOrDel("q", state.q);
  setOrDel("live", state.live === false ? "0" : state.live === true ? "1" : undefined);
  setOrDel("tz", state.tz === "utc" ? "utc" : state.tz === "local" ? "local" : undefined);
  const next = params.toString();
  const url = location.pathname + (next ? "?" + next : "") + location.hash;
  history.replaceState(null, "", url);
}

export function rangeFromUrl(url: UrlViewerState): RangeState {
  if (url.start && url.end) {
    const startMs = Date.parse(url.start);
    const endMs = Date.parse(url.end);
    if (Number.isFinite(startMs) && Number.isFinite(endMs) && endMs >= startMs) {
      const custom = { startMs, endMs };
      return { preset: "custom", custom, label: customLabel(custom) };
    }
  }
  const preset = url.preset && PRESETS.some((p) => p[0] === url.preset) ? url.preset : "24h";
  const label = PRESETS.find((p) => p[0] === preset)?.[1] || preset;
  return { preset, custom: null, label };
}

export function tickRolling(state: RangeState): RangeState {
  if (!isRollingPreset(state.preset, state.custom)) return state;
  return { ...state, label: PRESETS.find((p) => p[0] === state.preset)?.[1] || state.preset };
}
