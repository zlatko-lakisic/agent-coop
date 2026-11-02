/** Map scroll position <-> time for the sticky overview rail. */

import type { TimeRange } from "./range";

export type { TimeRange };

export type VisibleWindow = { topMs: number; bottomMs: number };

export type RailBucket = {
  start?: string;
  end?: string;
  actors?: Record<string, number>;
  counts?: Record<string, number>;
  markers?: string[];
  day_break?: boolean;
};

export function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function bucketHasActivity(bucket: RailBucket): boolean {
  const counts = bucket.actors || bucket.counts || {};
  for (const n of Object.values(counts)) {
    if (n > 0) return true;
  }
  return (bucket.markers || []).length > 0;
}

/**
 * Drop empty buckets before the first event and after the last.
 * Keep empty days in the middle so gaps stay visible, like the event list.
 */
export function trimEmptyRailEnds(buckets: RailBucket[]): RailBucket[] {
  let lo = 0;
  let hi = buckets.length - 1;
  while (lo <= hi && !bucketHasActivity(buckets[lo])) lo += 1;
  while (hi >= lo && !bucketHasActivity(buckets[hi])) hi -= 1;
  if (lo > hi) return [];
  return buckets.slice(lo, hi + 1);
}

/** Time span for a newest-first trimmed rail. Falls back when empty. */
export function railRangeFromBuckets(bucketsNewestFirst: RailBucket[], fallback: TimeRange): TimeRange {
  if (!bucketsNewestFirst.length) return fallback;
  const newest = bucketsNewestFirst[0];
  const oldest = bucketsNewestFirst[bucketsNewestFirst.length - 1];
  const endMs = Date.parse(String(newest.end || newest.start || "")) || fallback.endMs;
  const startMs = Date.parse(String(oldest.start || oldest.end || "")) || fallback.startMs;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    return fallback;
  }
  return { startMs, endMs };
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

/**
 * Visible window from event timestamps actually on screen in the middle timeline.
 * Falls back to scroll-fraction mapping when no stamped cards are in view.
 */
export function visibleWindowFromTimeline(timeline: HTMLElement, range: TimeRange): VisibleWindow {
  const viewTop = timeline.scrollTop;
  const viewBottom = viewTop + timeline.clientHeight;
  const nodes = timeline.querySelectorAll<HTMLElement>("[data-event-ts]");
  let newest: number | null = null;
  let oldest: number | null = null;
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const top = node.offsetTop;
    const bottom = top + Math.max(1, node.offsetHeight);
    if (bottom < viewTop || top > viewBottom) continue;
    const ms = Date.parse(node.getAttribute("data-event-ts") || "");
    if (!Number.isFinite(ms)) continue;
    if (newest == null || ms > newest) newest = ms;
    if (oldest == null || ms < oldest) oldest = ms;
  }
  if (newest == null || oldest == null) {
    return windowFromScroll(timeline.scrollTop, timeline.clientHeight, timeline.scrollHeight, range);
  }
  if (newest === oldest) {
    const pad = Math.max(60_000, (range.endMs - range.startMs) * 0.02);
    return {
      topMs: clamp(newest + pad, range.startMs, range.endMs),
      bottomMs: clamp(oldest - pad, range.startMs, range.endMs),
    };
  }
  return {
    topMs: clamp(newest, range.startMs, range.endMs),
    bottomMs: clamp(oldest, range.startMs, range.endMs),
  };
}

/** Keep the orange window inside the independently scrolling rail track. */
export function scrollRailToWindow(
  track: HTMLElement,
  window: VisibleWindow,
  range: TimeRange,
): void {
  const style = windowStyle(window, range);
  const top = (parseFloat(style.top) / 100) * track.scrollHeight;
  const height = (parseFloat(style.height) / 100) * track.scrollHeight;
  const bottom = top + height;
  const viewTop = track.scrollTop;
  const viewBottom = viewTop + track.clientHeight;
  if (top < viewTop) {
    track.scrollTop = Math.max(0, top - 12);
  } else if (bottom > viewBottom) {
    track.scrollTop = Math.max(0, bottom - track.clientHeight + 12);
  }
}

/** Click/drag Y on a scrolled rail track -> fraction 0..1 of the full range. */
export function railPointerFraction(track: HTMLElement, clientY: number): number {
  const rect = track.getBoundingClientRect();
  const y = clientY - rect.top + track.scrollTop;
  return clamp(y / Math.max(1, track.scrollHeight), 0, 1);
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

/** Scroll the middle timeline so the event nearest to ms is at the top. */
export function scrollTimelineToTime(timeline: HTMLElement, ms: number): boolean {
  const nodes = timeline.querySelectorAll<HTMLElement>("[data-event-ts]");
  let best: HTMLElement | null = null;
  let bestDist = Infinity;
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const t = Date.parse(node.getAttribute("data-event-ts") || "");
    if (!Number.isFinite(t)) continue;
    const dist = Math.abs(t - ms);
    if (dist < bestDist) {
      bestDist = dist;
      best = node;
    }
  }
  if (!best) {
    return false;
  }
  timeline.scrollTop = Math.max(0, best.offsetTop - 8);
  return true;
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

/** How many rail buckets fit when the rail can scroll on its own. */
export function bucketCountFromHeight(heightPx: number, rowPx = 14): number {
  // Prefer denser buckets than the viewport alone: the track scrolls like Frigate.
  const visible = Math.floor(Math.max(0, heightPx) / Math.max(10, rowPx));
  return Math.max(24, Math.min(200, Math.max(visible * 2, 48)));
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
