import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { agentColor, agentRadius, palette, type Palette } from "./palette";
import {
  scrollTopForTime,
  timeToRailFraction,
  windowFromScroll,
  windowStyle,
  mergeLiveEvents,
  type TimeRange,
} from "./rail";
import { ViewerSocket, type ConnState } from "./ws";

type EventRow = {
  id: string;
  ts?: string;
  order?: number;
  project?: string;
  actor?: string;
  kind?: string;
  title?: string;
  summary?: string;
  body?: string;
  severity?: string;
  refs?: Record<string, unknown>;
  attachments?: unknown[];
  marker?: string;
  percent?: number | string;
};

type AgentRow = {
  name: string;
  role?: string;
  state?: string;
  tags?: string[];
  grade?: string;
  seen?: string;
  last_seen?: string;
  activity?: { count?: number }[];
  display?: string;
};

type ProjectRow = { name: string; path?: string; driver?: string };

const PRESETS: [string, string, number | null][] = [
  ["1h", "Last hour", 3600_000],
  ["6h", "Last 6 hours", 6 * 3600_000],
  ["today", "Today", null],
  ["24h", "Last 24 hours", 24 * 3600_000],
  ["7d", "Last 7 days", 7 * 86400_000],
  ["30d", "Last 30 days", 30 * 86400_000],
  ["all", "All", null],
];

function applyTheme(c: Palette) {
  const root = document.documentElement;
  root.style.setProperty("--page", c.page);
  root.style.setProperty("--surface", c.surface);
  root.style.setProperty("--surface2", c.surface2);
  root.style.setProperty("--border", c.border);
  root.style.setProperty("--text", c.text);
  root.style.setProperty("--muted", c.muted);
  root.style.setProperty("--problem", c.problem);
  root.style.setProperty("--problem-bg", c.problemBg);
  root.style.setProperty("--attention", c.attention);
  root.style.setProperty("--attention-bg", c.attentionBg);
  root.style.setProperty("--accent", c.accent);
  root.style.setProperty("--accent-bg", c.accentBg);
}

function rangeForPreset(preset: string): TimeRange {
  const now = Date.now();
  if (preset === "all") return { startMs: now - 365 * 86400_000, endMs: now };
  if (preset === "today") {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return { startMs: d.getTime(), endMs: now };
  }
  const found = PRESETS.find((p) => p[0] === preset);
  const ms = found?.[2] ?? 24 * 3600_000;
  return { startMs: now - (ms || 24 * 3600_000), endMs: now };
}

function fmtRange(r: TimeRange): string {
  const a = new Date(r.startMs);
  const b = new Date(r.endMs);
  const opt: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };
  return a.toLocaleString(undefined, opt) + " to " + b.toLocaleString(undefined, opt);
}

function dayLabel(ts?: string): string {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function timeLabel(ts?: string): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

function kindIcon(kind: string): string {
  if (kind.includes("stall")) return "stall";
  if (kind.includes("review")) return "review";
  if (kind.includes("progress") || kind.includes("context")) return "prog";
  if (kind.includes("file")) return "file";
  if (kind.includes("membership") || kind.includes("join")) return "role";
  if (kind.includes("settings") || kind.includes("heartbeat") || kind.includes("round")) return "gear";
  return "msg";
}

function RobotMark({ c }: { c: Palette }) {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <line x1="14" y1="3" x2="14" y2="8" stroke={c.text} stroke-width="2" />
      <circle cx="14" cy="3.5" r="2.5" fill="#F2A33A" />
      <rect x="4" y="8" width="20" height="15" rx="5" fill={c.driver} stroke={c.text} stroke-width="1.5" />
      <circle cx="10.5" cy="15.5" r="2" fill="#FFFFFF" />
      <circle cx="17.5" cy="15.5" r="2" fill="#FFFFFF" />
    </svg>
  );
}

export function App({ token }: { token: string }) {
  const [dark, setDark] = useState(true);
  const c = useMemo(() => palette(dark), [dark]);
  useEffect(() => applyTheme(c), [c]);

  const [conn, setConn] = useState<ConnState>("connecting");
  const [live, setLive] = useState(true);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [project, setProject] = useState("");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [rules, setRules] = useState<Record<string, unknown>>({});
  const [files, setFiles] = useState<{ name: string; meta?: string; kind?: string; path?: string; url?: string; size?: string }[]>([]);
  const [statusItems, setStatusItems] = useState<{ kind: string; text: string; project?: string }[]>([]);
  const [histogram, setHistogram] = useState<{ buckets?: { start?: string; end?: string; counts?: Record<string, number>; markers?: string[] }[] }>({});
  const [tab, setTab] = useState<"agents" | "rules" | "files">("agents");
  const [filterActor, setFilterActor] = useState("");
  const [query, setQuery] = useState("");
  const [preset, setPreset] = useState("24h");
  const [rangeOpen, setRangeOpen] = useState(false);
  const [selected, setSelected] = useState<EventRow | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [sheet, setSheet] = useState<"agents" | "rules" | "files" | null>(null);
  const [pendingNew, setPendingNew] = useState(0);
  const [rawOpen, setRawOpen] = useState(false);
  const socketRef = useRef<ViewerSocket | null>(null);
  const timelineRef = useRef<HTMLElement | null>(null);
  const [winStyle, setWinStyle] = useState({ top: "0%", height: "20%" });
  const [railDayTop, setRailDayTop] = useState("");
  const [railDayBottom, setRailDayBottom] = useState("");
  const range = useMemo(() => rangeForPreset(preset), [preset]);
  const atTopRef = useRef(true);

  useEffect(() => {
    if (!token) return;
    const sock = new ViewerSocket(token, {
      onState: setConn,
      onHello: () => {
        void bootstrap(sock);
      },
      onPush: (msg) => handlePush(msg),
      onReply: () => undefined,
    });
    socketRef.current = sock;
    sock.start();
    return () => sock.stop();
  }, [token]);

  async function bootstrap(sock: ViewerSocket) {
    const proj = (await sock.request("projects")) as { projects: ProjectRow[] };
    setProjects(proj.projects || []);
    const name = project || proj.projects?.[0]?.name || "";
    setProject(name);
    await loadProject(sock, name);
  }

  async function loadProject(sock: ViewerSocket, name: string) {
    if (!name) return;
    const from = new Date(range.startMs).toISOString();
    const to = new Date(range.endMs).toISOString();
    const [ev, ag, ru, st, hi, fi] = await Promise.all([
      sock.request("events", { project: name, from, to, limit: 200, q: query || undefined }),
      sock.request("agents", { project: name }),
      sock.request("rules", { project: name }),
      sock.request("status"),
      sock.request("histogram", { project: name, from, to, buckets: 24 }),
      sock.request("files", { project: name }),
    ]);
    setEvents(((ev as { events: EventRow[] }).events) || []);
    setAgents(((ag as { agents: AgentRow[] }).agents) || []);
    setRules((ru as Record<string, unknown>) || {});
    setStatusItems(((st as { items: typeof statusItems }).items) || []);
    setHistogram(hi as typeof histogram);
    setFiles(((fi as { files: typeof files }).files) || []);
    await sock.request("subscribe", { project: name, from, to });
  }

  function handlePush(msg: Record<string, unknown>) {
    const type = String(msg.type || "");
    if (type === "event.added" || type === "event.updated") {
      const event = msg.event as EventRow;
      if (!event?.id) return;
      if (filterActor && event.actor !== filterActor) return;
      if (live && atTopRef.current) {
        setEvents((prev) => mergeLiveEvents(prev, [event]));
      } else {
        setPendingNew((n) => n + 1);
        setEvents((prev) => mergeLiveEvents(prev, [event]));
      }
      return;
    }
    if (type === "status") setStatusItems(((msg.data as { items: typeof statusItems })?.items) || []);
    if (type === "agents" && (!msg.project || msg.project === project)) {
      setAgents(((msg.data as { agents: AgentRow[] })?.agents) || []);
    }
    if (type === "rules" && (!msg.project || msg.project === project)) {
      setRules((msg.data as Record<string, unknown>) || {});
    }
    if (type === "histogram.delta" && (!msg.project || msg.project === project)) {
      setHistogram((msg.data as typeof histogram) || {});
    }
  }

  useEffect(() => {
    const sock = socketRef.current;
    if (!sock || conn !== "live" || !project) return;
    void loadProject(sock, project);
  }, [project, preset, query]);

  useEffect(() => {
    const el = timelineRef.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const win = windowFromScroll(el.scrollTop, el.clientHeight, el.scrollHeight, range);
      setWinStyle(windowStyle(win, range));
      atTopRef.current = el.scrollTop < 40;
      const visible = events.find((e) => {
        const t = e.ts ? Date.parse(e.ts) : 0;
        return t <= win.topMs && t >= win.bottomMs;
      });
      setRailDayTop(visible?.ts ? new Date(visible.ts).toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : "");
      const oldest = events[events.length - 1];
      setRailDayBottom(oldest?.ts ? new Date(oldest.ts).toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : "");
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [events, range]);

  async function openEvent(row: EventRow) {
    setSelected(row);
    setRawOpen(false);
    try {
      const d = await socketRef.current?.request("event", { id: row.id });
      setDetail((d as Record<string, unknown>) || row);
    } catch {
      setDetail(row);
    }
  }

  function scrubTo(frac: number) {
    const el = timelineRef.current;
    if (!el) return;
    const ms = range.endMs - frac * (range.endMs - range.startMs);
    el.scrollTop = scrollTopForTime(ms, el.clientHeight, el.scrollHeight, range);
  }

  const filtered = filterActor ? events.filter((e) => e.actor === filterActor) : events;
  const rows: ({ type: "day"; day: string; count: number } | { type: "event"; event: EventRow })[] = [];
  let lastDay = "";
  let dayCount = 0;
  const dayCounts = new Map<string, number>();
  for (const e of filtered) {
    const d = dayLabel(e.ts);
    dayCounts.set(d, (dayCounts.get(d) || 0) + 1);
  }
  for (const e of filtered) {
    const d = dayLabel(e.ts);
    if (d !== lastDay) {
      lastDay = d;
      dayCount = dayCounts.get(d) || 0;
      rows.push({ type: "day", day: d, count: dayCount });
    }
    rows.push({ type: "event", event: e });
  }

  const connDot = conn === "live" ? c.teal : conn === "reconnecting" ? c.attention : c.problem;
  const presetLabel = PRESETS.find((p) => p[0] === preset)?.[1] || preset;

  if (!token) {
    return (
      <div class="app" style={{ background: c.page, color: c.text }}>
        <div class="empty">
          <h2>Open the viewer from Agent Co-op</h2>
          <p>This page needs the link from <span class="mono">agentcoop ui</span>.</p>
        </div>
      </div>
    );
  }

  if (conn === "lost") {
    return (
      <div class="app" style={{ background: c.page, color: c.text }}>
        <div class="empty">
          <img alt="" src="./images/disconnected.svg" width="160" height="120" />
          <h2>Disconnected</h2>
          <p>The board viewer lost its live connection.</p>
          <button class="btn" type="button" onClick={() => location.reload()}>Reconnect</button>
        </div>
      </div>
    );
  }

  const buckets = histogram.buckets || [];

  return (
    <div class="app">
      <header class="topbar" ref={(n) => n && document.documentElement.style.setProperty("--topbar-h", n.offsetHeight + "px")}>
        <div class="brand">
          <RobotMark c={c} />
          <span>Agent Co-op</span>
        </div>
        <label class="btn" aria-label="Choose project">
          <span class="dot" style={{ background: c.driver, borderRadius: 2 }} />
          <select
            value={project}
            onChange={(e) => setProject((e.target as HTMLSelectElement).value)}
            style={{ border: 0, background: "transparent", color: "inherit" }}
          >
            {projects.map((p) => (
              <option value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
        <div style={{ position: "relative" }} class="hide-phone">
          <button
            type="button"
            class="btn"
            style={{ borderColor: rangeOpen ? c.accent : c.border }}
            aria-label="Choose date range"
            onClick={() => setRangeOpen((v) => !v)}
          >
            <span aria-hidden="true">📅</span>
            <strong>{presetLabel}</strong>
            <span style={{ color: c.muted }}>{fmtRange(range)}</span>
          </button>
          {rangeOpen && (
            <div
              style={{
                position: "absolute",
                top: 44,
                left: 0,
                zIndex: 20,
                display: "flex",
                gap: 16,
                padding: 16,
                background: c.surface,
                border: "1px solid " + c.border,
                borderRadius: 12,
                boxShadow: "0 12px 32px rgba(0,0,0,0.28)",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 4, width: 160 }}>
                {PRESETS.map(([id, label]) => (
                  <button
                    type="button"
                    key={id}
                    onClick={() => {
                      setPreset(id);
                      setRangeOpen(false);
                    }}
                    style={{
                      textAlign: "left",
                      height: 32,
                      padding: "0 10px",
                      borderRadius: 6,
                      border: 0,
                      color: preset === id ? c.text : c.muted,
                      fontWeight: preset === id ? 650 : 500,
                      background: preset === id ? c.surface2 : "transparent",
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div class="hide-phone" style={{ display: "flex", gap: 4 }}>
          <button type="button" class="btn icon ghost" aria-label="Previous range" onClick={() => setPreset("7d")}>‹</button>
          <button type="button" class="btn icon ghost" aria-label="Next range">›</button>
        </div>
        <button
          type="button"
          class="btn pill"
          aria-label="Live updates"
          style={{ background: live ? (dark ? "rgba(60,196,180,0.12)" : "rgba(19,120,110,0.08)") : "transparent" }}
          onClick={() => setLive((v) => !v)}
        >
          <span class="dot" style={{ background: live ? connDot : c.muted }} />
          Live
        </button>
        <div class="search hide-phone">
          <span aria-hidden="true">⌕</span>
          <label for="q" class="sr-only" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>Search the board</label>
          <input id="q" placeholder="Search messages, progress, files" value={query} onInput={(e) => setQuery((e.target as HTMLInputElement).value)} />
          <span class="kbd">/</span>
        </div>
        <button type="button" class="btn ghost hide-phone">Filters</button>
        <button type="button" class="btn icon ghost" aria-label="Switch light or dark" onClick={() => setDark((v) => !v)}>☾</button>
        <button type="button" class="btn ghost" onClick={() => setManageOpen(true)}>Manage</button>
      </header>

      <div class="attention" ref={(n) => n && document.documentElement.style.setProperty("--attn-h", n.offsetHeight + "px")}>
        <span style={{ fontSize: 12, color: c.muted, marginRight: 4 }}>Needs attention</span>
        {statusItems.map((item) => (
          <button
            type="button"
            class={"chip " + (item.kind.includes("stall") ? "problem" : "attention")}
            onClick={() => {
              const hit = events.find((e) => (e.summary || "").includes(item.text.slice(0, 24)) || e.kind?.includes("stall"));
              if (hit) void openEvent(hit);
            }}
          >
            {item.text}
          </button>
        ))}
        {!statusItems.length && <span style={{ color: c.muted, fontSize: 13 }}>Nothing waiting</span>}
      </div>

      <div class="body">
        <aside class={"aside" + (sheet ? " sheet" : "")} hidden={!!sheet ? false : undefined} style={sheet ? undefined : undefined}>
          {(sheet || true) && (
            <>
              <div class="tabs" role="tablist">
                {(["agents", "rules", "files"] as const).map((id) => (
                  <button type="button" role="tab" class={tab === id ? "on" : ""} onClick={() => setTab(id)}>
                    {id[0].toUpperCase() + id.slice(1)}
                  </button>
                ))}
                {sheet && (
                  <button type="button" class="btn icon ghost" aria-label="Close" onClick={() => setSheet(null)}>✕</button>
                )}
              </div>
              {tab === "agents" &&
                agents.map((a) => {
                  const on = filterActor === a.name;
                  const color = agentColor(c, a.name);
                  const spark = (a.activity || []).map((d) => Math.max(2, (d.count || 0) * 2));
                  while (spark.length < 7) spark.unshift(2);
                  return (
                    <button
                      type="button"
                      class={"card" + (on ? " on" : "")}
                      onClick={() => setFilterActor(on ? "" : a.name)}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span
                          style={{
                            width: 28,
                            height: 28,
                            borderRadius: agentRadius(a.name, a.role),
                            background: color,
                            color: "#fff",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontWeight: 700,
                            fontSize: 12,
                          }}
                        >
                          {(a.name || "?").slice(0, 1).toUpperCase()}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 600 }}>{a.display || a.name}</div>
                          <div style={{ fontSize: 12, color: c.muted }}>{a.role}</div>
                        </div>
                        <span style={{ fontSize: 11, fontWeight: 650, padding: "3px 8px", borderRadius: 999, color: c.muted, border: "1px solid " + c.border }}>
                          {a.state || "Idle"}
                        </span>
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                        {(a.tags || []).map((tg) => (
                          <span style={{ fontSize: 11, padding: "2px 7px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{tg}</span>
                        ))}
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: 12, color: c.muted }}>
                        <div>
                          <div>{a.grade}</div>
                          <div>{a.seen || a.last_seen}</div>
                        </div>
                        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 26 }} aria-hidden="true">
                          {spark.slice(-7).map((h) => (
                            <span style={{ width: 6, height: h, borderRadius: 2, background: color, opacity: 0.85 }} />
                          ))}
                        </div>
                      </div>
                    </button>
                  );
                })}
              {tab === "rules" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {[
                    ["Roles", ["Driver: " + String(rules.driver || ""), "Helpers: " + Object.keys((rules.helpers as object) || {}).join(", ")]],
                    ["What helpers may do", ((rules.may as string[]) || [])],
                    ["Review", ["Review: " + String(rules.review || "")]],
                    ["Checking", ["Checking: " + String(rules.checking || "")]],
                    ["Versions", ["Installed " + String(rules.version || "")]],
                  ].map(([group, lines]) => (
                    <div style={{ padding: 12, borderRadius: 10, border: "1px solid " + c.border, background: c.surface2 }}>
                      <div style={{ fontSize: 12, fontWeight: 650, color: c.muted, marginBottom: 6 }}>{group as string}</div>
                      {(lines as string[]).map((ln) => (
                        <div style={{ fontSize: 13, lineHeight: 1.5 }}>{ln}</div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
              {tab === "files" && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
                  {files.map((f) => (
                    <a href={f.path || f.url || "#"} style={{ borderRadius: 10, border: "1px solid " + c.border, overflow: "hidden", background: c.surface2, textDecoration: "none", color: "inherit" }}>
                      <div style={{ height: 76, background: c.surface, display: "flex", alignItems: "center", justifyContent: "center", color: c.muted, fontSize: 11 }}>{f.kind || "file"}</div>
                      <div style={{ padding: 8 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</div>
                        <div style={{ fontSize: 11, color: c.muted }}>{f.size || f.meta}</div>
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </>
          )}
        </aside>

        <main class="timeline" ref={timelineRef as never} aria-label="Board timeline">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "8px 0 4px" }}>
            <div style={{ fontSize: 13, color: c.muted }}>{filtered.length} events in this range. Newest first.</div>
            {filterActor && (
              <button type="button" class="chip" onClick={() => setFilterActor("")}>
                Showing {filterActor} only. Show everyone
              </button>
            )}
            {pendingNew > 0 && (
              <button
                type="button"
                class="chip"
                onClick={() => {
                  timelineRef.current && (timelineRef.current.scrollTop = 0);
                  setPendingNew(0);
                }}
              >
                {pendingNew} new events
              </button>
            )}
          </div>
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {rows.map((row) =>
              row.type === "day" ? (
                <li>
                  <div class="day-head">
                    <span style={{ fontWeight: 650, fontSize: 13 }}>{row.day}</span>
                    <span style={{ flex: 1, height: 1, background: c.border }} />
                    <span style={{ fontSize: 12, color: c.muted }}>{row.count} events</span>
                  </div>
                </li>
              ) : (
                <EventCard
                  key={row.event.id}
                  event={row.event}
                  c={c}
                  dark={dark}
                  selected={selected?.id === row.event.id}
                  onOpen={() => void openEvent(row.event)}
                />
              ),
            )}
          </ol>
          {!filtered.length && (
            <div class="empty">
              <img alt="" src="./images/empty-range.svg" width="160" height="120" />
              <p>No events in this range.</p>
            </div>
          )}
        </main>

        <nav
          class="rail"
          aria-label="Time overview"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") scrubTo(Math.min(1, timeToRailFraction(range.startMs + 1, range) + 0.04));
            if (e.key === "ArrowUp") scrubTo(Math.max(0, timeToRailFraction(range.endMs - 1, range) - 0.04));
          }}
          onClick={(e) => {
            const rect = (e.currentTarget as HTMLElement).querySelector(".rail-track")?.getBoundingClientRect();
            if (!rect) return;
            const frac = (e.clientY - rect.top) / Math.max(1, rect.height);
            scrubTo(frac);
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 650, color: c.muted, marginBottom: 8, textAlign: "center" }}>{railDayTop}</div>
          <div class="rail-track">
            <span class="rail-window" style={winStyle} aria-label="Shown in the timeline" />
            {buckets.map((b, i) => {
              const counts = b.counts || {};
              const total = Object.values(counts).reduce((a, n) => a + n, 0) || 1;
              const label = b.start ? new Date(b.start).getHours().toString().padStart(2, "0") : "";
              return (
                <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 4, height: 25 }}>
                  <span class={"mono hour"} style={{ width: 18, fontSize: 10, textAlign: "right", color: c.muted }}>
                    {i % 1 === 0 ? label : ""}
                  </span>
                  <span style={{ flex: 1, display: "flex", height: 12, borderRadius: 3, overflow: "hidden", background: c.surface2 }}>
                    {Object.entries(counts).map(([actor, n]) => (
                      <span style={{ width: (100 * n) / total + "%", background: agentColor(c, actor) }} />
                    ))}
                  </span>
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: (b.markers || []).length ? c.problem : "transparent",
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11, fontWeight: 650, color: c.muted, marginTop: 8, textAlign: "center" }}>{railDayBottom}</div>
        </nav>

        {selected && detail && (
          <section class="drawer" aria-label="Event details">
            <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: c.muted }}>{String(detail.ts || selected.ts || "")}</div>
                <h2 style={{ margin: "4px 0 0", fontSize: 18, lineHeight: 1.3 }}>{String(detail.title || selected.title)}</h2>
              </div>
              <button type="button" class="btn icon ghost" aria-label="Close details" onClick={() => { setSelected(null); setDetail(null); }}>✕</button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <span style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, background: dark ? "rgba(255,255,255,0.06)" : "rgba(30,27,75,0.06)", color: agentColor(c, String(detail.actor || "")), fontWeight: 600 }}>
                {String(detail.actor || "")}
              </span>
              <span style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{String(detail.kind || "")}</span>
              <span class="mono" style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{String(detail.id || selected.id)}</span>
            </div>
            <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-line" }}>{String(detail.body || detail.summary || "")}</div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 650, color: c.muted, marginBottom: 8 }}>Thread</div>
              <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                {((detail.thread as { ts?: string; actor?: string; title?: string; summary?: string }[]) || []).map((th) => (
                  <li style={{ display: "flex", gap: 10, fontSize: 13 }}>
                    <span class="mono" style={{ width: 44, flex: "0 0 44px", color: c.muted }}>{timeLabel(th.ts)}</span>
                    <span style={{ fontWeight: 600, color: agentColor(c, String(th.actor || "")) }}>{th.actor}</span>
                    <span style={{ color: c.muted }}>{th.title || th.summary}</span>
                  </li>
                ))}
              </ol>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
              <button
                type="button"
                class="btn ghost"
                onClick={() => void navigator.clipboard.writeText(String(detail.id || selected.id))}
              >
                Copy id
              </button>
              <button type="button" class="btn ghost" onClick={() => setRawOpen((v) => !v)}>
                {rawOpen ? "Hide raw JSON" : "Show raw JSON"}
              </button>
            </div>
            {rawOpen && (
              <pre class="mono" style={{ fontSize: 11, overflow: "auto", background: c.surface2, padding: 12, borderRadius: 8, border: "1px solid " + c.border }}>
                {JSON.stringify(detail, null, 2)}
              </pre>
            )}
          </section>
        )}
      </div>

      <div class="bottom-bar">
        <button type="button" class="on" onClick={() => setSheet(null)}>Timeline</button>
        <button type="button" onClick={() => { setTab("agents"); setSheet("agents"); }}>Agents</button>
        <button type="button" onClick={() => { setTab("rules"); setSheet("rules"); }}>Rules</button>
        <button type="button" onClick={() => { setTab("files"); setSheet("files"); }}>Files</button>
      </div>

      {manageOpen && (
        <ManageModal
          c={c}
          project={project}
          projects={projects}
          agents={agents}
          onClose={() => setManageOpen(false)}
          request={(op, params) => socketRef.current!.request(op, params)}
        />
      )}
    </div>
  );
}

function EventCard({
  event,
  c,
  dark,
  selected,
  onOpen,
}: {
  event: EventRow;
  c: Palette;
  dark: boolean;
  selected: boolean;
  onOpen: () => void;
}) {
  const sev = event.severity || "";
  const color =
    sev === "problem" || (event.kind || "").includes("stall")
      ? c.problem
      : (event.kind || "").includes("review")
        ? c.review
        : agentColor(c, event.actor || "");
  const edge =
    sev === "problem" || (event.kind || "").includes("stall")
      ? c.problem
      : sev === "attention" || (event.kind || "").includes("nudge")
        ? c.attention
        : selected
          ? c.accent
          : c.border;
  const icon = kindIcon(event.kind || "");
  const pct = Number(event.percent || 0);
  return (
    <li>
      <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}>
        <div class="mono" style={{ width: 48, flex: "0 0 48px", paddingTop: 14, textAlign: "right", fontSize: 12, color: c.muted }}>
          {timeLabel(event.ts)}
        </div>
        <div style={{ width: 16, flex: "0 0 16px", position: "relative", display: "flex", justifyContent: "center" }}>
          <span style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: c.border }} />
          <span
            style={{
              position: "relative",
              marginTop: 16,
              width: 10,
              height: 10,
              borderRadius: agentRadius(event.actor || ""),
              background: color,
              boxShadow: "0 0 0 3px " + c.page,
            }}
          />
        </div>
        <button
          type="button"
          class="ev"
          onClick={onOpen}
          style={{
            flex: 1,
            minWidth: 0,
            textAlign: "left",
            margin: "6px 0",
            padding: "12px 14px",
            borderRadius: 10,
            color: c.text,
            background: selected ? c.surface2 : c.surface,
            border: "1px solid " + edge,
            boxShadow: dark ? "none" : "0 1px 2px rgba(30,27,75,0.06)",
            transition: "transform 150ms ease",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: 6, background: dark ? "rgba(255,255,255,0.06)" : "rgba(30,27,75,0.05)", fontSize: 10 }}>
              {icon.slice(0, 1).toUpperCase()}
            </span>
            <span style={{ fontWeight: 600, color }}>{event.actor}</span>
            <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{event.kind}</span>
            <span style={{ fontWeight: 600 }}>{event.title}</span>
          </div>
          <div style={{ marginTop: 6, color: c.muted, lineHeight: 1.45 }}>{event.summary}</div>
          {pct > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
              <span style={{ flex: 1, height: 6, borderRadius: 999, background: c.border, overflow: "hidden" }}>
                <span style={{ display: "block", height: 6, width: pct + "%", background: color, borderRadius: 999 }} />
              </span>
              <span style={{ fontSize: 12, color: c.muted }}>{pct}%</span>
            </div>
          )}
        </button>
      </div>
    </li>
  );
}

function ManageModal({
  c,
  project,
  projects,
  agents,
  onClose,
  request,
}: {
  c: Palette;
  project: string;
  projects: ProjectRow[];
  agents: AgentRow[];
  onClose: () => void;
  request: (op: string, params: Record<string, unknown>) => Promise<unknown>;
}) {
  const [log, setLog] = useState("");
  const [path, setPath] = useState("");
  const [helper, setHelper] = useState(agents[0]?.name || "");
  const [driver, setDriver] = useState(projects.find((p) => p.name === project)?.driver || "");

  async function run(op: string, params: Record<string, unknown>) {
    try {
      const data = (await request(op, params)) as { text?: string };
      setLog(data.text || "Done.");
    } catch (err) {
      setLog(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div class="manage" role="dialog" aria-label="Manage">
      <div class="manage-panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Manage</h2>
          <button type="button" class="btn icon ghost" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <p style={{ margin: 0, color: c.muted }}>These actions used to live on the old viewer page.</p>
        <label>
          Change driver for {project}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <select value={driver} onChange={(e) => setDriver((e.target as HTMLSelectElement).value)}>
              {agents.map((a) => (
                <option value={a.name}>{a.name}</option>
              ))}
            </select>
            <button type="button" class="btn" onClick={() => void run("driver", { project, agent: driver })}>Set driver</button>
          </div>
        </label>
        <label>
          Add or remove helper
          <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
            <select value={helper} onChange={(e) => setHelper((e.target as HTMLSelectElement).value)}>
              {agents.map((a) => (
                <option value={a.name}>{a.name}</option>
              ))}
            </select>
            <button type="button" class="btn" onClick={() => void run("helper_add", { project, agent: helper, note: "helps the driver" })}>Add</button>
            <button type="button" class="btn ghost" onClick={() => void run("helper_remove", { project, agent: helper })}>Remove</button>
          </div>
        </label>
        <label>
          Add project path
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <input style={{ flex: 1 }} value={path} onInput={(e) => setPath((e.target as HTMLInputElement).value)} placeholder="C:\\Projects\\example" />
            <button type="button" class="btn" onClick={() => void run("project_add", { path, driver: driver || agents[0]?.name })}>Add</button>
          </div>
        </label>
        <button type="button" class="btn ghost" onClick={() => void run("shutdown", {})}>Close viewer</button>
        {log && <pre style={{ whiteSpace: "pre-wrap", background: c.surface2, padding: 12, borderRadius: 8, border: "1px solid " + c.border }}>{log}</pre>}
      </div>
    </div>
  );
}
