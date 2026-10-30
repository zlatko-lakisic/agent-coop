import { afterEach, describe, expect, it, vi } from "vitest";
import {
  customLabel,
  rangeForPreset,
  rangeFromUrl,
  readUrlState,
  resolveRange,
  shiftRange,
} from "./range";

describe("rolling presets", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps Last hour ending at now after the clock moves", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-28T15:00:00Z"));
    const first = rangeForPreset("1h");
    expect(first.endMs).toBe(Date.parse("2026-10-28T15:00:00Z"));
    vi.setSystemTime(new Date("2026-10-28T15:10:00Z"));
    const second = rangeForPreset("1h");
    expect(second.endMs).toBe(Date.parse("2026-10-28T15:10:00Z"));
    expect(second.startMs).toBe(second.endMs - 3600_000);
  });

  it("shifts prev/next by the window length into a custom label", () => {
    const range = { startMs: Date.parse("2026-10-27T14:00:00Z"), endMs: Date.parse("2026-10-28T14:00:00Z") };
    const prev = shiftRange(range, -1);
    expect(prev.endMs - prev.startMs).toBe(range.endMs - range.startMs);
    expect(prev.endMs).toBe(range.startMs);
    expect(customLabel(prev)).toContain("to");
  });

  it("keeps a custom range through URL round-trip", () => {
    const start = "2026-10-27T14:00:00.000Z";
    const end = "2026-10-28T14:00:00.000Z";
    const state = rangeFromUrl({ start, end, preset: "7d" });
    expect(state.custom?.startMs).toBe(Date.parse(start));
    expect(state.custom?.endMs).toBe(Date.parse(end));
    expect(resolveRange(state).endMs).toBe(Date.parse(end));
  });

  it("reads URL state without a token field", () => {
    const read = readUrlState("?project=demo&preset=6h&live=1&tz=utc&t=secret");
    expect(read.project).toBe("demo");
    expect(read.preset).toBe("6h");
    expect(read.tz).toBe("utc");
    expect((read as { t?: string }).t).toBeUndefined();
  });
});
