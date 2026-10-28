import { describe, expect, it } from "vitest";
import {
  mergeLiveEvents,
  railFractionToTime,
  reconnectDelay,
  timeToRailFraction,
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
