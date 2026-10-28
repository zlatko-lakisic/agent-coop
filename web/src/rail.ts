/** Map scroll position <-> time for the sticky overview rail. */

export type TimeRange = { startMs: number; endMs: number };

export type VisibleWindow = { topMs: number; bottomMs: number };

export function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** Fraction 0 = newest (top of rail), 1 = oldest (bottom). */
export function timeToRailFraction(ms: number, range: TimeRange): number {
  const span = Math.max(1, range.endMs - range.startMs);
  // Newest at top: endMs -> 0, startMs -> 1
  return clamp((range.endMs - ms) / span, 0, 1);
}

export function railFractionToTime(frac: number, range: TimeRange): number {
  const span = Math.max(1, range.endMs - range.startMs);
  return range.endMs - clamp(frac, 0, 1) * span;
}

export function windowFromScroll(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  range: TimeRange,
): VisibleWindow {
  const maxScroll = Math.max(1, scrollHeight - clientHeight);
  const topFrac = clamp(scrollTop / maxScroll, 0, 1);
  const bottomFrac = clamp((scrollTop + clientHeight) / Math.max(1, scrollHeight), 0, 1);
  // Timeline is newest-first: scrollTop 0 shows newest times near range.endMs
  const topMs = railFractionToTime(topFrac, range);
  const bottomMs = railFractionToTime(bottomFrac, range);
  return { topMs: Math.max(topMs, bottomMs), bottomMs: Math.min(topMs, bottomMs) };
}

export function scrollTopForTime(
  ms: number,
  clientHeight: number,
  scrollHeight: number,
  range: TimeRange,
): number {
  const maxScroll = Math.max(0, scrollHeight - clientHeight);
  const frac = timeToRailFraction(ms, range);
  return clamp(frac * maxScroll, 0, maxScroll);
}

export function windowStyle(
  window: VisibleWindow,
  range: TimeRange,
): { top: string; height: string } {
  const topFrac = timeToRailFraction(window.topMs, range);
  const bottomFrac = timeToRailFraction(window.bottomMs, range);
  const top = Math.min(topFrac, bottomFrac);
  const height = Math.max(0.02, Math.abs(bottomFrac - topFrac));
  return { top: top * 100 + "%", height: height * 100 + "%" };
}

export function reconnectDelay(attempt: number): number {
  const steps = [1000, 2000, 4000, 8000, 30000];
  return steps[Math.min(attempt, steps.length - 1)];
}

export function mergeLiveEvents<T extends { id: string; order?: number; ts?: string }>(
  existing: T[],
  incoming: T[],
): T[] {
  const byId = new Map<string, T>();
  for (const row of existing) byId.set(row.id, row);
  for (const row of incoming) byId.set(row.id, row);
  return Array.from(byId.values()).sort((a, b) => {
    const ao = a.order ?? 0;
    const bo = b.order ?? 0;
    if (ao !== bo) return bo - ao;
    return String(b.ts || "").localeCompare(String(a.ts || ""));
  });
}
