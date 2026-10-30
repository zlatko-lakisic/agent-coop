/** Plain kind labels, progress field parsing, and word-boundary ellipsis. */

export function kindLabel(kind: string): string {
  const map: Record<string, string> = {
    "progress.update": "Progress",
    "progress.review": "Review",
    "progress.waiting": "Waiting",
    "progress.done": "Done",
    "stall.progress": "Stall",
    "stall.mail": "Mail stall",
    "update.check": "Update check",
    "update.installed": "Update",
    "message.cancel": "Cancel",
    "message.nudge": "Nudge",
    "message.question": "Question",
    "message.answer": "Answer",
    "message.task": "Task",
    "message.status": "Status",
    "message.ack": "Ack",
    "message.patch": "Patch",
    "message.context_request": "Context request",
    "message.ping": "Ping",
    "message.heartbeat": "Heartbeat",
    "message.decline": "Decline",
    "message.review_request": "Review request",
    "message.review_verdict": "Review verdict",
    "message.move": "Move",
    "file.shared": "File",
    "file.held": "Held file",
    "context.version": "Context",
    "membership.join": "Join",
    "membership.driver": "Driver",
    "membership.helper_add": "Helper added",
    "membership.helper_remove": "Helper removed",
    "settings.watch": "Checking",
    "settings.review": "Review setting",
    "settings.notice": "Notice",
    "settings.approval": "Approval",
    "settings.override": "Override",
    "outbox.sent": "Outbox",
    "outbox.rejected": "Rejected",
    "grade.late": "Late grade",
    "grade.answer": "Grade",
    "round.log": "Round",
    "trace.line": "Trace",
    "review.metric": "Review",
  };
  if (map[kind]) return map[kind];
  const part = kind.split(".").pop() || kind;
  return part.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export type ProgressFields = {
  status: string;
  step: string;
  percent: number | null;
  now: string;
  title: string;
  barLabel: string;
};

export function parseProgressFields(body: string, fallbackTitle = ""): ProgressFields {
  const lines = (body || "").split(/\r?\n/);
  const fields: Record<string, string> = {};
  for (const line of lines) {
    const m = line.match(/^(Status|Step|Percent|Now)\s*:\s*(.*)$/i);
    if (m) fields[m[1].toLowerCase()] = m[2].trim();
  }
  const status = fields.status || "";
  const step = fields.step || "";
  const now = fields.now || "";
  let percent: number | null = null;
  if (fields.percent != null && fields.percent !== "") {
    const n = Number(String(fields.percent).replace(/%/g, ""));
    if (Number.isFinite(n)) percent = n;
  }
  const title = now || fallbackTitle || "Progress";
  let barLabel = "";
  if (step && percent != null) barLabel = "Step " + step + ", " + percent + "%";
  else if (step) barLabel = "Step " + step;
  else if (percent != null) barLabel = percent + "%";
  return { status, step, percent, now, title, barLabel };
}

export function ellipsisWords(text: string, max = 140): string {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const boundary = cut.lastIndexOf(" ");
  const base = boundary > max * 0.5 ? cut.slice(0, boundary) : cut;
  return base.replace(/[.,;:]+$/, "") + "…";
}

export function eventCountLabel(n: number): string {
  return n === 1 ? "1 event" : n + " events";
}

export function dayKey(ts?: string, timeZone?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (timeZone === "UTC") {
    return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0");
  }
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

export function dayLabel(ts?: string, timeZone?: string): string {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: timeZone === "UTC" ? "UTC" : undefined,
  });
}

export function timeLabel(ts?: string, timeZone?: string): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timeZone === "UTC" ? "UTC" : undefined,
  });
}

export function drawerTimeLabel(ts?: string, timeZone?: string): string {
  if (!ts) return "";
  return new Date(ts).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timeZone === "UTC" ? "UTC" : undefined,
  });
}

export function displayActor(actor?: string): string {
  if (!actor || actor === "board") return "Agent Co-op";
  return actor;
}
