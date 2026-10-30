import { describe, expect, it } from "vitest";
import { dayKey, ellipsisWords, eventCountLabel, kindLabel, parseProgressFields } from "./labels";
import { bucketCountFromHeight, mergeLiveEvents } from "./rail";

describe("labels and order", () => {
  it("uses plain kind labels", () => {
    expect(kindLabel("progress.update")).toBe("Progress");
    expect(kindLabel("stall.progress")).toBe("Stall");
    expect(kindLabel("update.check")).toBe("Update check");
    expect(kindLabel("update.installed")).toBe("Update");
    expect(kindLabel("message.cancel")).toBe("Cancel");
  });

  it("parses progress fields without raw Status text", () => {
    const fields = parseProgressFields("Status: working\nStep: 2 of 5\nPercent: 40\nNow: paint the sky\n");
    expect(fields.status).toBe("working");
    expect(fields.title).toBe("paint the sky");
    expect(fields.barLabel).toBe("Step 2 of 5, 40%");
    expect(fields.percent).toBe(40);
  });

  it("ellipsis at a word boundary and singular event count", () => {
    expect(ellipsisWords("one two three four five", 12)).toBe("one two…");
    expect(eventCountLabel(1)).toBe("1 event");
    expect(eventCountLabel(3)).toBe("3 events");
  });

  it("day keys include the year", () => {
    expect(dayKey("2025-10-28T12:00:00Z", "UTC")).toBe("2025-10-28");
    expect(dayKey("2026-10-28T12:00:00Z", "UTC")).toBe("2026-10-28");
  });

  it("merges mixed synthetic and message order newest first", () => {
    const merged = mergeLiveEvents(
      [
        { id: "msg", order: 2, ts: "2026-10-28T12:00:02Z" },
        { id: "stall", order: 1, ts: "2026-10-28T12:00:01Z" },
      ],
      [{ id: "prog", order: 3, ts: "2026-10-28T12:00:03Z" }],
    );
    expect(merged.map((e) => e.id)).toEqual(["prog", "msg", "stall"]);
  });

  it("sizes rail buckets from height", () => {
    expect(bucketCountFromHeight(250)).toBe(10);
    expect(bucketCountFromHeight(0)).toBe(8);
  });
});
