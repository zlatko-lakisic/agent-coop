import { reconnectDelay } from "./rail";

export type ConnState = "connecting" | "live" | "reconnecting" | "lost";

export type WsHandlers = {
  onState: (state: ConnState) => void;
  onHello: (fileSession: string, readonly: boolean) => void;
  onPush: (message: Record<string, unknown>) => void;
  onReply: (message: Record<string, unknown>) => void;
};

export class ViewerSocket {
  private ws: WebSocket | None = null;
  private token: string;
  private handlers: WsHandlers;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private attempt = 0;
  private lastSeq = 0;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(token: string, handlers: WsHandlers) {
    this.token = token;
    this.handlers = handlers;
  }

  get seq(): number {
    return this.lastSeq;
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  request(op: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.ws?.send(JSON.stringify({ id, op, params }));
      } catch (err) {
        this.pending.delete(id);
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  private connect(): void {
    if (this.stopped) return;
    this.handlers.onState(this.attempt === 0 ? "connecting" : "reconnecting");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = proto + "//" + location.host + "/";
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", token: this.token }));
    };
    ws.onmessage = (ev) => {
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (message.type === "hello.ok") {
        this.attempt = 0;
        this.handlers.onState("live");
        this.handlers.onHello(String(message.file_session || ""), Boolean(message.readonly));
        if (this.lastSeq > 0) {
          void this.request("resume", { seq: this.lastSeq }).then((data) => {
            const payload = data as { messages?: Record<string, unknown>[] };
            for (const row of payload.messages || []) this.handlers.onPush(row);
          }).catch(() => {
            /* reload handled by caller via error text */
          });
        }
        return;
      }
      if (typeof message.seq === "number") {
        this.lastSeq = Math.max(this.lastSeq, message.seq as number);
      }
      if ("id" in message && message.id != null) {
        const pending = this.pending.get(Number(message.id));
        if (pending) {
          this.pending.delete(Number(message.id));
          if (message.ok) pending.resolve(message.data);
          else pending.reject(new Error(String(message.error || "Request failed")));
        }
        this.handlers.onReply(message);
        return;
      }
      if (message.type) this.handlers.onPush(message);
    };
    ws.onclose = () => {
      for (const [, p] of this.pending) p.reject(new Error("Disconnected"));
      this.pending.clear();
      if (this.stopped) {
        this.handlers.onState("lost");
        return;
      }
      this.handlers.onState("reconnecting");
      const delay = reconnectDelay(this.attempt++);
      this.timer = setTimeout(() => this.connect(), delay);
      if (this.attempt > 8) this.handlers.onState("lost");
    };
    ws.onerror = () => {
      ws.close();
    };
  }
}

type None = null;
