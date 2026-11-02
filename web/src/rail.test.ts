import { describe, expect, it } from "vitest";
import {
  mergeLiveEvents,
  railFractionToTime,
  railPointerFraction,
  railRangeFromBuckets,
  reconnectDelay,
  timeToRailFraction,
  trimEmptyRailEnds,
  visibleWindowFromTimeline,
  windowFromScroll,
  windowStyle,
} from "./rail";

const range = { startMs: 0, endMs: 1000 };

describe("rail mapping", () => {
  it("maps newest to the top of the rail", () => {
    expect(timeToRailFraction(1000, range)).toBe(0);
    expect(timeToRailFraction(0, range)).toBe(1);
    expect(railFractionToTime(0, range)).toBe(1000);
    expect(railFractionToTime(1, range)).toBe(0);
  });

  it("sizes the orange window from scroll", () => {
    const win = windowFromScroll(0, 100, 400, range);
    expect(win.topMs).toBeGreaterThan(win.bottomMs);
    const style = windowStyle(win, range);
    expect(parseFloat(style.top)).toBeLessThan(50);
    expect(parseFloat(style.height)).toBeGreaterThan(0);
  });

  it("trims empty rail ends but keeps gaps between activity", () => {
    const trimmed = trimEmptyRailEnds([
      { start: "a", actors: {} },
      { start: "b", actors: { cursor: 1 } },
      { start: "c", actors: {} },
      { start: "d", actors: { helper: 2 } },
      { start: "e", actors: {} },
    ]);
    expect(trimmed.map((b) => b.start)).toEqual(["b", "c", "d"]);
    const newestFirst = [
      { start: "2026-10-22T00:00:00Z", end: "2026-11-01T12:00:00Z", actors: { cursor: 1 } },
      { start: "2026-10-10T00:00:00Z", end: "2026-10-22T00:00:00Z", actors: { cursor: 1 } },
    ];
    const span = railRangeFromBuckets(newestFirst, range);
    expect(span.endMs).toBe(Date.parse("2026-11-01T12:00:00Z"));
    expect(span.startMs).toBe(Date.parse("2026-10-10T00:00:00Z"));
  });

  it("builds the orange window from visible event timestamps", () => {
    const cards = [
      { offsetTop: 0, offsetHeight: 40, getAttribute: () => "1970-01-01T00:00:00.800Z" },
      { offsetTop: 50, offsetHeight: 40, getAttribute: () => "1970-01-01T00:00:00.200Z" },
      { offsetTop: 500, offsetHeight: 40, getAttribute: () => "1970-01-01T00:00:00.000Z" },
    ];
    const timeline = {
      scrollTop: 0,
      clientHeight: 100,
      scrollHeight: 600,
      querySelectorAll: () => cards,
    } as unknown as HTMLElement;
    const win = visibleWindowFromTimeline(timeline, range);
    expect(win.topMs).toBe(800);
    expect(win.bottomMs).toBe(200);
  });

  it("maps pointer Y on a scrolled track to a fraction of the full range", () => {
    const track = {
      getBoundingClientRect: () => ({ top: 100, height: 200, left: 0, right: 40, width: 40, bottom: 300, x: 0, y: 100, toJSON: () => ({}) }),
      scrollTop: 50,
      scrollHeight: 400,
    } as unknown as HTMLElement;
    // clientY 100 -> y = 0 + 50 = 50 -> 50/400 = 0.125
    expect(railPointerFraction(track, 100)).toBeCloseTo(0.125);
    expect(railPointerFraction(track, 1000)).toBe(1);
  });

  it("uses reconnect backoff caps", () => {
    expect(reconnectDelay(0)).toBe(1000);
    expect(reconnectDelay(4)).toBe(30000);
    expect(reconnectDelay(99)).toBe(30000);
  });

  it("merges live events without duplicates", () => {
    const merged = mergeLiveEvents(
      [
        { id: "a", order: 2 },
        { id: "b", order: 1 },
      ],
      [
        { id: "c", order: 3 },
        { id: "a", order: 2 },
      ],
    );
    expect(merged.map((e) => e.id)).toEqual(["c", "a", "b"]);
  });
});
