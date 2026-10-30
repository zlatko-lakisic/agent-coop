import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { KindIcon, iconForKind } from "./icons";
import {
  dayKey,
  dayLabel,
  displayActor,
  drawerTimeLabel,
  ellipsisWords,
  eventCountLabel,
  kindLabel,
  parseProgressFields,
  timeLabel,
} from "./labels";
import { agentColor, agentRadius, palette, type Palette } from "./palette";
import {
  PRESETS,
  customLabel,
  fmtRange,
  rangeFromUrl,
  readUrlState,
  resolveRange,
  shiftRange,
  writeUrlState,
  type RangeState,
} from "./range";
import {
  bucketCountFromHeight,
  mergeLiveEvents,
  scrollTopForTime,
  timeToRailFraction,
  windowFromScroll,
  windowStyle,
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

type FileRow = {
  id?: string;
  name: string;
  meta?: string;
  kind?: string;
  bucket?: string;
  path?: string;
  url?: string;
  size?: string;
  ts?: string;
  actor?: string;
  note?: string;
  served?: boolean;
  source?: string;
  messages?: { id?: string; actor?: string; ts?: string }[];
};

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

function actorTone(c: Palette, actor?: string): string {
  const name = displayActor(actor);
  if (name === "Agent Co-op") return c.system;
  return agentColor(c, name);
}

export function App({ token }: { token: string }) {
  const initialUrl = useMemo(() => readUrlState(), []);
  const [dark, setDark] = useState(true);
  const c = useMemo(() => palette(dark), [dark]);
  useEffect(() => applyTheme(c), [c]);

  const [conn, setConn] = useState<ConnState>("connecting");
  const [live, setLive] = useState(initialUrl.live !== false);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [project, setProject] = useState(initialUrl.project || "");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [eventTotal, setEventTotal] = useState(0);
  const [dayCounts, setDayCounts] = useState<Record<string, number>>({});
  const [nextCursor, setNextCursor] = useState("");
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [rules, setRules] = useState<Record<string, unknown>>({});
  const [files, setFiles] = useState<FileRow[]>([]);
  const [fileFilter, setFileFilter] = useState<"all" | "image" | "document" | "other">("all");
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [statusItems, setStatusItems] = useState<{ kind: string; text: string; project?: string }[]>([]);
  const [histogram, setHistogram] = useState<{ buckets?: { start?: string; end?: string; actors?: Record<string, number>; counts?: Record<string, number>; markers?: string[] }[] }>({});
  const [tab, setTab] = useState<"agents" | "rules" | "files">("agents");
  const [filterActor, setFilterActor] = useState("");
  const [query, setQuery] = useState(initialUrl.q || "");
  const [rangeState, setRangeState] = useState<RangeState>(() => rangeFromUrl(initialUrl));
  const [nowMs, setNowMs] = useState(Date.now());
  const [tzMode, setTzMode] = useState<"local" | "utc">(initialUrl.tz || "local");
  const [rangeOpen, setRangeOpen] = useState(false);
  const [calAnchor, setCalAnchor] = useState(() => new Date());
  const [calStart, setCalStart] = useState<string | null>(null);
  const [fromTime, setFromTime] = useState("00:00");
  const [toTime, setToTime] = useState("23:59");
  const [selected, setSelected] = useState<EventRow | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [sheet, setSheet] = useState<"agents" | "rules" | "files" | null>(null);
  const [pendingNew, setPendingNew] = useState(0);
  const [rawOpen, setRawOpen] = useState(false);
  const [readonly, setReadonly] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const socketRef = useRef<ViewerSocket | null>(null);
  const timelineRef = useRef<HTMLElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const focusCardRef = useRef<HTMLButtonElement | null>(null);
  const [winStyle, setWinStyle] = useState({ top: "0%", height: "20%" });
  const [railDayTop, setRailDayTop] = useState("");
  const [railDayBottom, setRailDayBottom] = useState("");
  const [bucketCount, setBucketCount] = useState(24);
  const atTopRef = useRef(true);
  const preserveScrollRef = useRef(true);

  const projectRef = useRef(project);
  const rangeStateRef = useRef(rangeState);
  const liveRef = useRef(live);
  const queryRef = useRef(query);
  const filterActorRef = useRef(filterActor);
  const nowRef = useRef(nowMs);
  projectRef.current = project;
  rangeStateRef.current = rangeState;
  liveRef.current = live;
  queryRef.current = query;
  filterActorRef.current = filterActor;
  nowRef.current = nowMs;

  const range = useMemo(() => resolveRange(rangeState, nowMs), [rangeState, nowMs]);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const tz = tzMode === "utc" ? "UTC" : undefined;

  useEffect(() => {
    if (!token) return;
    const sock = new ViewerSocket(token, {
      onState: setConn,
      onHello: (_fileSession, isReadonly) => {
        setReadonly(isReadonly);
        void bootstrap(sock);
      },
      onPush: (msg) => handlePush(msg),
      onReply: () => undefined,
    });
    socketRef.current = sock;
    sock.start();
    return () => sock.stop();
  }, [token]);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [live]);

  useEffect(() => {
    writeUrlState({
      project: project || undefined,
      preset: rangeState.custom ? undefined : rangeState.preset,
      start: rangeState.custom ? new Date(rangeState.custom.startMs).toISOString() : undefined,
      end: rangeState.custom ? new Date(rangeState.custom.endMs).toISOString() : undefined,
      q: query || undefined,
      live,
      tz: tzMode,
    });
  }, [project, rangeState, query, live, tzMode]);

  async function bootstrap(sock: ViewerSocket) {
    const proj = (await sock.request("projects")) as { projects: ProjectRow[] };
    setProjects(proj.projects || []);
    const name = projectRef.current || proj.projects?.[0]?.name || "";
    setProject(name);
    await loadProject(sock, name, true);
  }

  async function loadProject(sock: ViewerSocket, name: string, resubscribe: boolean) {
    if (!name) return;
    const current = resolveRange(rangeStateRef.current, nowRef.current);
    const from = new Date(current.startMs).toISOString();
    const to = new Date(current.endMs).toISOString();
    const buckets = bucketCount;
    const [ev, ag, ru, st, hi, fi] = await Promise.all([
      sock.request("events", { project: name, from, to, limit: 50, q: queryRef.current || undefined, actors: filterActorRef.current ? [filterActorRef.current] : undefined }),
      sock.request("agents", { project: name }),
      sock.request("rules", { project: name }),
      sock.request("status"),
      sock.request("histogram", { project: name, from, to, buckets }),
      sock.request("files", { project: name, from, to }),
    ]);
    const page = ev as { events: EventRow[]; next?: string; total?: number; day_counts?: Record<string, number> };
    setEvents(page.events || []);
    setNextCursor(page.next || "");
    setEventTotal(page.total ?? (page.events || []).length);
    setDayCounts(page.day_counts || {});
    setAgents(((ag as { agents: AgentRow[] }).agents) || []);
    setRules((ru as Record<string, unknown>) || {});
    setStatusItems(((st as { items: typeof statusItems }).items) || []);
    setHistogram(hi as typeof histogram);
    setFiles(((fi as { files: FileRow[] }).files) || []);
    if (resubscribe) {
      await sock.request("subscribe", {
        project: name,
        from,
        to,
        actors: filterActorRef.current ? [filterActorRef.current] : undefined,
      });
    }
  }

  function handlePush(msg: Record<string, unknown>) {
    const type = String(msg.type || "");
    if (type === "event.added" || type === "event.updated") {
      const event = msg.event as EventRow;
      if (!event?.id) return;
      if (filterActorRef.current && event.actor !== filterActorRef.current) return;
      if (liveRef.current && atTopRef.current) {
        setEvents((prev) => mergeLiveEvents(prev, [event]));
        setEventTotal((n) => n + (type === "event.added" ? 1 : 0));
      } else {
        setPendingNew((n) => n + 1);
        setEvents((prev) => mergeLiveEvents(prev, [event]));
      }
      return;
    }
    if (type === "status") setStatusItems(((msg.data as { items: typeof statusItems })?.items) || []);
    if (type === "agents" && (!msg.project || msg.project === projectRef.current)) {
      setAgents(((msg.data as { agents: AgentRow[] })?.agents) || []);
    }
    if (type === "rules" && (!msg.project || msg.project === projectRef.current)) {
      setRules((msg.data as Record<string, unknown>) || {});
    }
    if (type === "histogram.delta" && (!msg.project || msg.project === projectRef.current)) {
      setHistogram((msg.data as typeof histogram) || {});
    }
    if (type === "files" && (!msg.project || msg.project === projectRef.current)) {
      setFiles(((msg.data as { files: FileRow[] })?.files) || []);
    }
  }

  useEffect(() => {
    const sock = socketRef.current;
    if (!sock || conn !== "live" || !project) return;
    void loadProject(sock, project, true);
  }, [project, rangeState, query, filterActor, bucketCount, conn]);

  useEffect(() => {
    const sock = socketRef.current;
    if (!sock || conn !== "live" || !project || !live || rangeState.custom) return;
    const current = resolveRange(rangeStateRef.current, nowMs);
    void sock.request("subscribe", {
      project,
      from: new Date(current.startMs).toISOString(),
      to: new Date(current.endMs).toISOString(),
      actors: filterActorRef.current ? [filterActorRef.current] : undefined,
    });
  }, [nowMs, live, conn, project, rangeState.custom]);

  useEffect(() => {
    const el = railRef.current;
    if (!el) return;
    const update = () => setBucketCount(bucketCountFromHeight(el.clientHeight));
    update();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    ro?.observe(el);
    window.addEventListener("resize", update);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, []);

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
      setRailDayTop(visible?.ts ? new Date(visible.ts).toLocaleDateString(undefined, { weekday: "short", day: "numeric", timeZone: tz }) : "");
      const oldest = events[events.length - 1];
      setRailDayBottom(oldest?.ts ? new Date(oldest.ts).toLocaleDateString(undefined, { weekday: "short", day: "numeric", timeZone: tz }) : "");
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
      if (el.scrollTop + el.clientHeight > el.scrollHeight - 120) void loadMore();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [events, range, tz, nextCursor]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selected) {
        setSelected(null);
        setDetail(null);
        focusCardRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  async function loadMore() {
    const sock = socketRef.current;
    if (!sock || !nextCursor || loadingMore || !project) return;
    setLoadingMore(true);
    try {
      const current = rangeRef.current;
      const from = new Date(current.startMs).toISOString();
      const to = new Date(current.endMs).toISOString();
      const page = (await sock.request("events", {
        project,
        from,
        to,
        limit: 50,
        cursor: nextCursor,
        q: queryRef.current || undefined,
        actors: filterActorRef.current ? [filterActorRef.current] : undefined,
      })) as { events: EventRow[]; next?: string; total?: number; day_counts?: Record<string, number> };
      setEvents((prev) => mergeLiveEvents(prev, page.events || []));
      setNextCursor(page.next || "");
      if (page.total != null) setEventTotal(page.total);
      if (page.day_counts) setDayCounts((prev) => ({ ...prev, ...page.day_counts }));
    } finally {
      setLoadingMore(false);
    }
  }

  async function openEvent(row: EventRow, card?: HTMLButtonElement | null) {
    if (card) focusCardRef.current = card;
    setSelected(row);
    setRawOpen(false);
    try {
      const d = await socketRef.current?.request("event", { id: row.id });
      setDetail((d as Record<string, unknown>) || row);
    } catch {
      setDetail(row);
    }
  }

  async function scrubTo(frac: number) {
    const el = timelineRef.current;
    const sock = socketRef.current;
    if (!el || !sock || !project) return;
    const ms = range.endMs - frac * (range.endMs - range.startMs);
    preserveScrollRef.current = false;
    // Load pages until an event near that time exists or no next page.
    let guard = 0;
    while (guard++ < 40) {
      const hit = events.some((e) => e.ts && Math.abs(Date.parse(e.ts) - ms) < (range.endMs - range.startMs) / 20);
      if (hit || !nextCursor) break;
      await loadMore();
    }
    el.scrollTop = scrollTopForTime(ms, el.clientHeight, el.scrollHeight, range);
  }

  function choosePreset(id: string) {
    const label = PRESETS.find((p) => p[0] === id)?.[1] || id;
    setRangeState({ preset: id, custom: null, label });
    setRangeOpen(false);
  }

  function shiftWindow(direction: -1 | 1) {
    const next = shiftRange(range, direction);
    setRangeState({ preset: "custom", custom: next, label: customLabel(next) });
  }

  function applyCalendar(endDay: string) {
    if (!calStart) {
      setCalStart(endDay);
      return;
    }
    const startDay = calStart <= endDay ? calStart : endDay;
    const finishDay = calStart <= endDay ? endDay : calStart;
    const startMs = Date.parse(startDay + "T" + fromTime + ":00");
    const endMs = Date.parse(finishDay + "T" + toTime + ":00");
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return;
    const custom = { startMs: Math.min(startMs, endMs), endMs: Math.max(startMs, endMs) };
    setRangeState({ preset: "custom", custom, label: customLabel(custom) });
    setCalStart(null);
    setRangeOpen(false);
  }

  const filtered = filterActor ? events.filter((e) => e.actor === filterActor) : events;
  const rows: ({ type: "day"; day: string; key: string; count: number } | { type: "event"; event: EventRow })[] = [];
  let lastKey = "";
  for (const e of filtered) {
    const key = dayKey(e.ts, tz);
    if (key !== lastKey) {
      lastKey = key;
      const isoDay = (e.ts || "").slice(0, 10);
      const count = dayCounts[isoDay] || filtered.filter((row) => dayKey(row.ts, tz) === key).length;
      rows.push({ type: "day", day: dayLabel(e.ts, tz), key, count });
    }
    rows.push({ type: "event", event: e });
  }

  const connDot = conn === "live" ? c.teal : conn === "reconnecting" ? c.attention : c.problem;
  const presetLabel = rangeState.custom ? rangeState.label : PRESETS.find((p) => p[0] === rangeState.preset)?.[1] || rangeState.preset;
  const imageFiles = files.filter((f) => (f.bucket || f.kind || "").includes("image") && f.url);
  const shownFiles = files.filter((f) => fileFilter === "all" || f.bucket === fileFilter || (fileFilter === "image" && (f.kind || "").includes("image")));

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
          <select value={project} onChange={(e) => setProject((e.target as HTMLSelectElement).value)} style={{ border: 0, background: "transparent", color: "inherit" }}>
            {projects.map((p) => (
              <option value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
        <div style={{ position: "relative" }} class="hide-phone">
          <button type="button" class="btn" style={{ borderColor: rangeOpen ? c.accent : c.border }} aria-label="Choose date range" onClick={() => setRangeOpen((v) => !v)}>
            <span aria-hidden="true">📅</span>
            <strong>{presetLabel}</strong>
            <span style={{ color: c.muted }}>{fmtRange(range, tz)}</span>
          </button>
          {rangeOpen && (
            <div class="range-panel">
              <div style={{ display: "flex", flexDirection: "column", gap: 4, width: 160 }}>
                {PRESETS.map(([id, label]) => (
                  <button type="button" key={id} onClick={() => choosePreset(id)} style={{ textAlign: "left", height: 32, padding: "0 10px", borderRadius: 6, border: 0, color: rangeState.preset === id && !rangeState.custom ? c.text : c.muted, fontWeight: rangeState.preset === id && !rangeState.custom ? 650 : 500, background: rangeState.preset === id && !rangeState.custom ? c.surface2 : "transparent" }}>
                    {label}
                  </button>
                ))}
                <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                  <button type="button" class={"btn" + (tzMode === "local" ? "" : " ghost")} onClick={() => setTzMode("local")}>Local</button>
                  <button type="button" class={"btn" + (tzMode === "utc" ? "" : " ghost")} onClick={() => setTzMode("utc")}>UTC</button>
                </div>
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <button type="button" class="btn icon ghost" onClick={() => setCalAnchor(new Date(calAnchor.getFullYear(), calAnchor.getMonth() - 1, 1))}>‹</button>
                  <strong>{calAnchor.toLocaleString(undefined, { month: "long", year: "numeric" })}</strong>
                  <button type="button" class="btn icon ghost" onClick={() => setCalAnchor(new Date(calAnchor.getFullYear(), calAnchor.getMonth() + 1, 1))}>›</button>
                </div>
                <div class="cal-grid">
                  {Array.from({ length: 42 }, (_, i) => {
                    const first = new Date(calAnchor.getFullYear(), calAnchor.getMonth(), 1);
                    const startPad = (first.getDay() + 6) % 7;
                    const dayNum = i - startPad + 1;
                    const date = new Date(calAnchor.getFullYear(), calAnchor.getMonth(), dayNum);
                    const iso = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
                    const inMonth = date.getMonth() === calAnchor.getMonth();
                    const future = date.getTime() > Date.now();
                    const selectedDay = calStart === iso || (rangeState.custom && iso >= new Date(rangeState.custom.startMs).toISOString().slice(0, 10) && iso <= new Date(rangeState.custom.endMs).toISOString().slice(0, 10));
                    return (
                      <button type="button" class={"cal-day" + (selectedDay ? " on" : "") + (!inMonth || future ? " dim" : "")} disabled={!inMonth || future} onClick={() => applyCalendar(iso)}>
                        {date.getDate()}
                      </button>
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <label>From <input value={fromTime} onInput={(e) => setFromTime((e.target as HTMLInputElement).value)} /></label>
                  <label>To <input value={toTime} onInput={(e) => setToTime((e.target as HTMLInputElement).value)} /></label>
                </div>
              </div>
            </div>
          )}
        </div>
        <div class="hide-phone" style={{ display: "flex", gap: 4 }}>
          <button type="button" class="btn icon ghost" aria-label="Previous range" onClick={() => shiftWindow(-1)}>‹</button>
          <button type="button" class="btn icon ghost" aria-label="Next range" onClick={() => shiftWindow(1)}>›</button>
        </div>
        <button type="button" class="btn pill" aria-label="Live updates" style={{ background: live ? (dark ? "rgba(60,196,180,0.12)" : "rgba(19,120,110,0.08)") : "transparent" }} onClick={() => setLive((v) => !v)}>
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
        {!readonly && <button type="button" class="btn ghost" onClick={() => setManageOpen(true)}>Manage</button>}
      </header>

      <div class="attention" ref={(n) => n && document.documentElement.style.setProperty("--attn-h", n.offsetHeight + "px")}>
        <span style={{ fontSize: 12, color: c.muted, marginRight: 4 }}>Needs attention</span>
        {statusItems.map((item) => (
          <button type="button" class={"chip " + (item.kind.includes("stall") ? "problem" : "attention")} onClick={() => {
            const hit = events.find((e) => (e.summary || "").includes(item.text.slice(0, 24)) || e.kind?.includes("stall") || e.kind?.includes("waiting"));
            if (hit) void openEvent(hit);
          }}>
            {item.text}
          </button>
        ))}
        {!statusItems.length && <span style={{ color: c.muted, fontSize: 13 }}>Nothing waiting</span>}
      </div>

      <div class="body">
        <aside class={"aside" + (sheet ? " sheet" : "")}>
          <div class="tabs" role="tablist">
            {(["agents", "rules", "files"] as const).map((id) => (
              <button type="button" role="tab" class={tab === id ? "on" : ""} onClick={() => setTab(id)}>{id[0].toUpperCase() + id.slice(1)}</button>
            ))}
            {sheet && <button type="button" class="btn icon ghost" aria-label="Close" onClick={() => setSheet(null)}>✕</button>}
          </div>
          {tab === "agents" && agents.map((a) => {
            const on = filterActor === a.name;
            const color = agentColor(c, a.name);
            const spark = (a.activity || []).map((d) => Math.max(2, (d.count || 0) * 2));
            while (spark.length < 7) spark.unshift(2);
            return (
              <button type="button" class={"card" + (on ? " on" : "")} onClick={() => setFilterActor(on ? "" : a.name)}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ width: 28, height: 28, borderRadius: agentRadius(a.name, a.role), background: color, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12 }}>{(a.name || "?").slice(0, 1).toUpperCase()}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{a.display || a.name}</div>
                    <div style={{ fontSize: 12, color: c.muted }}>{a.role}</div>
                  </div>
                  <span class="status-pill">{a.state === "stalled" ? "Stalled" : a.state === "waiting" ? "Waiting" : a.state || "Idle"}</span>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>{(a.tags || []).map((tg) => <span style={{ fontSize: 11, padding: "2px 7px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{tg}</span>)}</div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: 12, color: c.muted }}>
                  <div><div>{a.grade}</div><div>{a.seen || a.last_seen}</div></div>
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 26 }} aria-hidden="true">{spark.slice(-7).map((h) => <span style={{ width: 6, height: h, borderRadius: 2, background: color, opacity: 0.85 }} />)}</div>
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
                  {(lines as string[]).map((ln) => <div style={{ fontSize: 13, lineHeight: 1.5 }}>{ln}</div>)}
                </div>
              ))}
            </div>
          )}
          {tab === "files" && (
            <div>
              <div class="chips-row">
                {(["all", "image", "document", "other"] as const).map((id) => (
                  <button type="button" class={"chip" + (fileFilter === id ? " attention" : "")} onClick={() => setFileFilter(id)}>{id === "all" ? "All" : id[0].toUpperCase() + id.slice(1) + (id === "image" ? "s" : id === "document" ? "s" : "")}</button>
                ))}
              </div>
              <div class="file-grid">
                {shownFiles.map((f) => (
                  <button type="button" class="file-tile" onClick={() => {
                    if (f.url && (f.bucket === "image" || (f.kind || "").includes("image"))) {
                      const idx = imageFiles.findIndex((img) => img.url === f.url);
                      setLightbox(idx >= 0 ? idx : 0);
                    } else if (f.messages?.[0]?.id) {
                      const hit = events.find((e) => e.id === f.messages?.[0]?.id);
                      if (hit) void openEvent(hit);
                    }
                  }}>
                    <div class="file-thumb">
                      {f.url && (f.bucket === "image" || (f.kind || "").includes("image")) ? <img src={f.url} alt="" /> : <KindIcon name="file" color={c.muted} />}
                    </div>
                    <div style={{ padding: 8 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</div>
                      <div style={{ fontSize: 11, color: c.muted }}>{f.kind || f.source} · {f.size || f.meta || f.note}</div>
                      <div style={{ fontSize: 11, color: c.muted }}>{displayActor(f.actor)}{(f.messages || []).length > 1 ? " · " + (f.messages || []).length + " messages" : ""}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>

        <main class="timeline" ref={timelineRef as never} aria-label="Board timeline">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "8px 0 4px" }}>
            <div style={{ fontSize: 13, color: c.muted }}>{eventCountLabel(eventTotal)} in this range. Newest first.</div>
            {filterActor && <button type="button" class="chip" onClick={() => setFilterActor("")}>Showing {filterActor} only. Show everyone</button>}
            {pendingNew > 0 && (
              <button type="button" class="chip" onClick={() => { timelineRef.current && (timelineRef.current.scrollTop = 0); setPendingNew(0); }}>
                {pendingNew} new events
              </button>
            )}
          </div>
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {rows.map((row) =>
              row.type === "day" ? (
                <li key={"day-" + row.key}>
                  <div class="day-head">
                    <span style={{ fontWeight: 650, fontSize: 13 }}>{row.day}</span>
                    <span style={{ flex: 1, height: 1, background: c.border }} />
                    <span style={{ fontSize: 12, color: c.muted }}>{eventCountLabel(row.count)}</span>
                  </div>
                </li>
              ) : (
                <EventCard key={row.event.id} event={row.event} c={c} dark={dark} selected={selected?.id === row.event.id} tz={tz} onOpen={(btn) => void openEvent(row.event, btn)} />
              ),
            )}
          </ol>
          {loadingMore && <div style={{ color: c.muted, padding: 12 }}>Loading more…</div>}
          {!filtered.length && (
            <div class="empty">
              <img alt="" src="./images/empty-range.svg" width="160" height="120" />
              <p>No events in this range.</p>
            </div>
          )}
        </main>

        <nav
          class="rail"
          ref={railRef as never}
          aria-label="Time overview"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") void scrubTo(Math.min(1, timeToRailFraction(range.startMs + 1, range) + 0.04));
            if (e.key === "ArrowUp") void scrubTo(Math.max(0, timeToRailFraction(range.endMs - 1, range) - 0.04));
          }}
          onClick={(e) => {
            const rect = (e.currentTarget as HTMLElement).querySelector(".rail-track")?.getBoundingClientRect();
            if (!rect) return;
            const frac = (e.clientY - rect.top) / Math.max(1, rect.height);
            void scrubTo(frac);
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 650, color: c.muted, marginBottom: 8, textAlign: "center" }}>{railDayTop}</div>
          <div class="rail-track">
            <span class="rail-window" style={winStyle} aria-label="Shown in the timeline" />
            {buckets.map((b, i) => {
              const counts = b.actors || b.counts || {};
              const total = Object.values(counts).reduce((a, n) => a + n, 0) || 1;
              const label = b.start ? new Date(b.start).getHours().toString().padStart(2, "0") : "";
              return (
                <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 4, height: 25 }}>
                  <span class={"mono hour"} style={{ width: 18, fontSize: 10, textAlign: "right", color: c.muted }}>{i % 1 === 0 ? label : ""}</span>
                  <span style={{ flex: 1, display: "flex", height: 12, borderRadius: 3, overflow: "hidden", background: c.surface2 }}>
                    {Object.entries(counts).map(([actor, n]) => (
                      <span style={{ width: (100 * n) / total + "%", background: actorTone(c, actor) }} />
                    ))}
                  </span>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: (b.markers || []).length ? c.problem : "transparent" }} />
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11, fontWeight: 650, color: c.muted, marginTop: 8, textAlign: "center" }}>{railDayBottom}</div>
        </nav>

        {selected && detail && (
          <section class="drawer" aria-label="Event details">
            <div class="drawer-scroll">
              <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, color: c.muted }} title={String(detail.ts || selected.ts || "") + " UTC"}>
                    {drawerTimeLabel(String(detail.ts || selected.ts || ""), tz)}
                  </div>
                  <h2 style={{ margin: "4px 0 0", fontSize: 18, lineHeight: 1.3 }}>{String(detail.title || selected.title)}</h2>
                </div>
                <button type="button" class="btn icon ghost" aria-label="Close details" onClick={() => { setSelected(null); setDetail(null); focusCardRef.current?.focus(); }}>✕</button>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                <span style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, background: dark ? "rgba(255,255,255,0.06)" : "rgba(30,27,75,0.06)", color: actorTone(c, String(detail.actor || "")), fontWeight: 600 }}>
                  {displayActor(String(detail.actor || ""))}
                </span>
                <span style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{kindLabel(String(detail.kind || ""))}</span>
                <span class="mono" style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{String(detail.id || selected.id)}</span>
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-line" }}>{String(detail.body || detail.summary || "")}</div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 650, color: c.muted, marginBottom: 8 }}>Thread</div>
                <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                  {((detail.thread as { ts?: string; actor?: string; title?: string; summary?: string }[]) || []).map((th) => (
                    <li style={{ display: "flex", gap: 10, fontSize: 13 }}>
                      <span class="mono" style={{ width: 44, flex: "0 0 44px", color: c.muted }}>{timeLabel(th.ts, tz)}</span>
                      <span style={{ fontWeight: 600, color: actorTone(c, String(th.actor || "")) }}>{displayActor(th.actor)}</span>
                      <span style={{ color: c.muted }}>{th.title || th.summary}</span>
                    </li>
                  ))}
                </ol>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
                <button type="button" class="btn ghost" onClick={() => void navigator.clipboard.writeText(String(detail.id || selected.id))}>Copy id</button>
                <button type="button" class="btn ghost" onClick={() => setRawOpen((v) => !v)}>{rawOpen ? "Hide raw JSON" : "Show raw JSON"}</button>
              </div>
              {rawOpen && (
                <pre class="mono" style={{ fontSize: 11, overflow: "auto", background: c.surface2, padding: 12, borderRadius: 8, border: "1px solid " + c.border }}>
                  {JSON.stringify(detail, null, 2)}
                </pre>
              )}
            </div>
          </section>
        )}
      </div>

      <div class="bottom-bar">
        <button type="button" class="on" onClick={() => setSheet(null)}>Timeline</button>
        <button type="button" onClick={() => { setTab("agents"); setSheet("agents"); }}>Agents</button>
        <button type="button" onClick={() => { setTab("rules"); setSheet("rules"); }}>Rules</button>
        <button type="button" onClick={() => { setTab("files"); setSheet("files"); }}>Files</button>
      </div>

      {lightbox != null && imageFiles[lightbox] && (
        <div class="lightbox" role="dialog" aria-label="Image">
          <button type="button" class="btn" onClick={() => setLightbox((lightbox + imageFiles.length - 1) % imageFiles.length)}>Prev</button>
          <img src={imageFiles[lightbox].url} alt={imageFiles[lightbox].name} />
          <button type="button" class="btn" onClick={() => setLightbox((lightbox + 1) % imageFiles.length)}>Next</button>
          <button type="button" class="btn" onClick={() => setLightbox(null)}>Close</button>
        </div>
      )}

      {manageOpen && (
        <ManageModal c={c} project={project} projects={projects} agents={agents} onClose={() => setManageOpen(false)} request={(op, params) => socketRef.current!.request(op, params)} />
      )}
    </div>
  );
}

function EventCard({
  event,
  c,
  dark,
  selected,
  tz,
  onOpen,
}: {
  event: EventRow;
  c: Palette;
  dark: boolean;
  selected: boolean;
  tz?: string;
  onOpen: (btn: HTMLButtonElement | null) => void;
}) {
  const sev = event.severity || "";
  const color =
    sev === "problem" || (event.kind || "").includes("stall")
      ? c.problem
      : (event.kind || "").includes("review")
        ? c.review
        : actorTone(c, event.actor);
  const edge =
    sev === "problem" || (event.kind || "").includes("stall")
      ? c.problem
      : sev === "attention" || (event.kind || "").includes("nudge")
        ? c.attention
        : selected
          ? c.accent
          : c.border;
  const icon = iconForKind(event.kind || "");
  const progress = (event.kind || "").startsWith("progress.") ? parseProgressFields(String(event.body || event.summary || ""), event.title) : null;
  const title = progress?.title || event.title;
  const summary = progress ? "" : ellipsisWords(event.summary || "");
  const pct = progress?.percent ?? (Number(event.percent || 0) || null);
  return (
    <li>
      <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}>
        <div class="mono" style={{ width: 48, flex: "0 0 48px", paddingTop: 14, textAlign: "right", fontSize: 12, color: c.muted }}>
          {timeLabel(event.ts, tz)}
        </div>
        <div style={{ width: 16, flex: "0 0 16px", position: "relative", display: "flex", justifyContent: "center" }}>
          <span style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: c.border }} />
          <span style={{ position: "relative", marginTop: 16, width: 10, height: 10, borderRadius: agentRadius(event.actor || ""), background: color, boxShadow: "0 0 0 3px " + c.page }} />
        </div>
        <button
          type="button"
          class="ev"
          onClick={(e) => onOpen(e.currentTarget as HTMLButtonElement)}
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
            <span style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: 6, background: dark ? "rgba(255,255,255,0.06)" : "rgba(30,27,75,0.05)" }}>
              <KindIcon name={icon} color={color} />
            </span>
            <span style={{ fontWeight: 600, color }}>{displayActor(event.actor)}</span>
            <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, border: "1px solid " + c.border, color: c.muted }}>{kindLabel(event.kind || "")}</span>
            {progress?.status && <span class="status-pill">{progress.status}</span>}
            <span style={{ fontWeight: 600 }}>{title}</span>
          </div>
          {summary && <div style={{ marginTop: 6, color: c.muted, lineHeight: 1.45 }}>{summary}</div>}
          {pct != null && pct > 0 && (
            <div class="pct-bar">
              <span class="pct-track"><span class="pct-fill" style={{ width: pct + "%", background: color }} /></span>
              <span style={{ fontSize: 12, color: c.muted }}>{progress?.barLabel || pct + "%"}</span>
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
              {agents.map((a) => <option value={a.name}>{a.name}</option>)}
            </select>
            <button type="button" class="btn" onClick={() => void run("driver", { project, agent: driver })}>Set driver</button>
          </div>
        </label>
        <label>
          Add or remove helper
          <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
            <select value={helper} onChange={(e) => setHelper((e.target as HTMLSelectElement).value)}>
              {agents.map((a) => <option value={a.name}>{a.name}</option>)}
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
