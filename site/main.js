import { ROLE, initials, byStatus, ringAgents, worksWithText } from "./agents.js";

const DEBUG = false;
const ORBIT_MS = 80000;
const TILT0 = (-12 * Math.PI) / 180;
let reduced = false;
let orbit = null;
const qs = (s, el = document) => el.querySelector(s);
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const easeOut = (t) => 1 - (1 - t) * (1 - t);
const fine = () => matchMedia("(hover: hover) and (pointer: fine)").matches;

function initNoJs() {
  document.documentElement.classList.replace("no-js", "js");
}

function prefersReducedMotion() {
  const mq = matchMedia("(prefers-reduced-motion: reduce)");
  reduced = mq.matches;
  mq.addEventListener("change", () => {
    reduced = mq.matches;
    if (orbit) orbit.reset();
  });
}

function markEl(a) {
  const s = document.createElement("span");
  if (a.icon) {
    s.className = "mark";
    s.style.setProperty("--icon", "url('./" + a.icon + "')");
    if (a.brand) s.dataset.brand = a.brand;
  } else {
    s.className = "mono";
    s.textContent = initials(a.name);
  }
  return s;
}

function initHeader() {
  const h = qs(".site-header");
  const on = () => h.classList.toggle("is-solid", scrollY > 40);
  addEventListener("scroll", on, { passive: true });
  on();
}

function initCopy() {
  const btn = qs("[data-copy]");
  const live = qs("[data-copy-live]");
  const box = qs("#setup-sentence");
  if (!btn || !box) return;
  const text = box.textContent.trim();
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M6.2 11.4 2.8 8l1.2-1.2 2.2 2.2 5.6-5.6L13 4.6z"/></svg> Copied';
      if (live) live.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = "Copy";
        if (live) live.textContent = "";
      }, 2000);
    } catch {
      const r = document.createRange();
      r.selectNodeContents(box);
      getSelection().removeAllRanges();
      getSelection().addRange(r);
      btn.textContent = "Press Ctrl+C or Cmd+C";
      if (live) live.textContent = btn.textContent;
    }
  });
}

function chipEl(a) {
  const li = document.createElement("li");
  li.className = "chip";
  li.style.setProperty("--tint", a.tint);
  const mark = markEl(a);
  mark.setAttribute("aria-hidden", "true");
  li.append(mark, document.createTextNode(a.name));
  return li;
}

function initToolGroups() {
  const sr = qs("[data-works-with]");
  if (sr) sr.textContent = worksWithText();
  document.querySelectorAll("[data-agents]").forEach((ul) => {
    const list = byStatus(ul.getAttribute("data-agents"));
    if (list.length) ul.replaceChildren(...list.map(chipEl));
  });
}

function initSeats() {
  const seats = [...document.querySelectorAll(".seat")];
  const cards = [...document.querySelectorAll(".step-card")];
  if (!seats.length) return;
  const fill = (i) => seats[i] && seats[i].classList.add("is-filled");
  if (reduced) return seats.forEach((_, i) => fill(i));
  const wide = matchMedia("(min-width: 1024px)");
  let done = false;
  const io = new IntersectionObserver((ents) => {
    for (const e of ents) {
      if (!e.isIntersecting) continue;
      if (wide.matches) {
        if (done) return;
        done = true;
        seats.forEach((_, i) => setTimeout(() => fill(i), i * 350));
      } else fill(cards.indexOf(e.target));
    }
  }, { threshold: 0.6 });
  if (wide.matches) {
    const s = qs("#start");
    if (s) io.observe(s);
  } else cards.forEach((c) => io.observe(c));
}

function initOrbit() {
  const root = qs(".orbit");
  if (!root) return;
  const planet = qs(".planet", root);
  const word = qs(".wordmark", root);
  const back = qs(".ring-back", root);
  const front = qs(".ring-front", root);
  const hold = qs(".orbit-nodes", root);
  const notesEl = qs(".notes", root);
  let nodes, rx, ry, cx, cy, born = 0, last = 0, raf = 0, running = false, paused = false, hoverT = 0, nextN = 0;
  let angle = 0, tilt = TILT0, want = TILT0, rg = [];
  const notes = [];
  const ns = "http://www.w3.org/2000/svg";

  function layout() {
    const vw = innerWidth, vh = innerHeight;
    const R = clamp(Math.min(0.24 * vw, 0.22 * vh), 90, 170);
    const font = R * 0.42;
    const node = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--node")) || 44;
    const g = vw < 640 ? 12 : vw < 1024 ? 16 : 24;
    rx = Math.min(2.6 * R, vw / 2 - node / 2 - g);
    ry = Math.max(rx * 0.28, font + node / 2 + 8);
    cx = root.clientWidth / 2;
    cy = root.clientHeight / 2;
    planet.style.width = planet.style.height = R * 2 + "px";
    planet.style.margin = -R + "px 0 0 " + -R + "px";
    word.style.fontSize = font + "px";
    rings();
  }

  function tiltRing() {
    const t = "translate(" + cx + " " + cy + ") rotate(" + (tilt * 180) / Math.PI + ")";
    for (let i = 0; i < rg.length; i++) rg[i].setAttribute("transform", t);
  }

  function rings() {
    const w = root.clientWidth, h = root.clientHeight;
    rg = [];
    for (const svg of [back, front]) {
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.setAttribute("width", w);
      svg.setAttribute("height", h);
    }
    function arc(svg, sx, sy, sw) {
      svg.replaceChildren();
      const g = document.createElementNS(ns, "g");
      [1, 0.96, 0.92].forEach((k, i) => {
        const p = document.createElementNS(ns, "path");
        p.setAttribute("d", "M " + sx * rx * k + " " + sy * ry * k + " A " + rx * k + " " + ry * k + " 0 0 " + sw + " " + -sx * rx * k + " " + -sy * ry * k);
        p.style.opacity = String(0.9 - i * 0.22);
        g.append(p);
      });
      svg.append(g);
      rg.push(g);
    }
    arc(back, -1, 0, 1);
    arc(front, 1, 0, 1);
    tiltRing();
  }

  function at(t) {
    const x = rx * Math.cos(t), y = ry * Math.sin(t);
    const X = x * Math.cos(tilt) - y * Math.sin(tilt);
    const Y = x * Math.sin(tilt) + y * Math.cos(tilt);
    const d = Math.sin(t), d01 = (d + 1) / 2;
    return { X, Y, d, s: 0.78 + 0.22 * d01, o: 0.62 + 0.38 * d01, z: d > 0 ? 60 + Math.round(d01 * 10) : 40 - Math.round((1 - d01) * 10) };
  }

  function onEnter(it) {
    if (!fine()) return;
    paused = true;
    it.h = 1.1;
    it.label.classList.remove("visually-hidden");
    const m = qs(".mark", it.el);
    if (m && m.dataset.brand) {
      m.style.backgroundColor = m.dataset.brand;
      m.style.opacity = "1";
    }
  }

  function onLeave(it) {
    if (!fine()) return;
    it.h = 1;
    it.label.classList.add("visually-hidden");
    clearTimeout(hoverT);
    hoverT = setTimeout(() => { paused = false; }, 400);
    const m = qs(".mark", it.el);
    if (m) {
      m.style.backgroundColor = "";
      m.style.opacity = "";
    }
  }

  function build() {
    hold.replaceChildren();
    nodes = ringAgents(innerWidth < 640 ? 6 : innerWidth < 1024 ? 7 : undefined).map((a) => {
      const el = document.createElement("span");
      el.className = "node";
      el.style.setProperty("--tint", a.tint);
      const label = document.createElement("span");
      label.className = "node-label visually-hidden";
      label.textContent = a.name + " · " + ROLE[a.status];
      el.append(markEl(a), label);
      hold.append(el);
      const it = { el, a, label, h: 1, b: 1 };
      el.onpointerenter = () => onEnter(it);
      el.onpointerleave = () => onLeave(it);
      el.onclick = () => {
        if (fine()) return;
        label.classList.remove("visually-hidden");
        setTimeout(() => label.classList.add("visually-hidden"), 2000);
      };
      return it;
    });
  }

  function place(now) {
    const n = nodes.length;
    nodes.forEach((it, i) => {
      const grow = reduced || !born ? 1 : easeOut(Math.min(1, Math.max(0, (now - born - i * 70) / 900)));
      const p = at(angle + (i * 2 * Math.PI) / n);
      it.p = p;
      it.el.style.opacity = reduced ? "1" : String(p.o * grow);
      it.el.style.zIndex = String(p.z);
      it.el.style.transform = "translate3d(" + (cx + p.X * grow) + "px," + (cy + p.Y * grow) + "px,0) translate(-50%,-50%) scale(" + p.s * it.b * it.h + ")";
    });
    if (word && born && !reduced) {
      const w = Math.min(1, (now - born) / 500);
      word.style.opacity = String(w);
      word.style.transform = "translate(-50%, calc(-50% + " + 12 * (1 - easeOut(w)) + "px))";
    }
    if (DEBUG) {
      const wm = word.getBoundingClientRect();
      nodes.forEach((it) => {
        if (!it.p || it.p.d <= 0) return;
        const b = it.el.getBoundingClientRect();
        if (!(b.right < wm.left || b.left > wm.right || b.bottom < wm.top || b.top > wm.bottom)) {
          console.warn("node overlaps wordmark", innerWidth, innerHeight);
        }
      });
    }
  }

  function qbez(a, b, t) {
    const c = { x: (a.x + b.x) * 0.35, y: (a.y + b.y) * 0.35 }, u = 1 - t;
    return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
  }

  function spawn(now) {
    const leads = nodes.filter((n) => n.a.status === "lead");
    if (leads.length < 1 || nodes.length < 2 || notes.length >= (innerWidth >= 1024 ? 2 : 1)) return;
    const a = leads[(Math.random() * leads.length) | 0];
    let b = a;
    for (let i = 0; i < 8 && b === a; i++) b = nodes[(Math.random() * nodes.length) | 0];
    if (b === a) return;
    const el = document.createElement("span");
    el.className = "note-dot";
    notesEl.append(el);
    notes.push({ el, from: a, to: b, t: now, k: 0 });
  }

  function tickNotes(now) {
    if (reduced || paused) return;
    if (now > nextN) { spawn(now); nextN = now + 3200; }
    for (let i = notes.length - 1; i >= 0; i--) {
      const n = notes[i], a = n.from.p, b = n.to.p;
      if (!a || !b) continue;
      const p0 = { x: cx + a.X, y: cy + a.Y }, mid = { x: cx, y: cy }, p2 = { x: cx + b.X, y: cy + b.Y };
      let pt;
      if (n.k === 0) {
        const t = Math.min(1, (now - n.t) / 600);
        pt = qbez(p0, mid, t);
        if (t >= 1) { n.k = 1; n.t = now; planet.classList.add("pulse-on"); }
      } else if (n.k === 1) {
        pt = mid;
        if (now - n.t >= 150) { n.k = 2; n.t = now; planet.classList.remove("pulse-on"); }
      } else {
        const t = Math.min(1, (now - n.t) / 600);
        pt = qbez(mid, p2, t);
        if (t >= 1) {
          n.to.b = 1.08;
          setTimeout(() => { n.to.b = 1; }, 180);
          n.el.remove();
          notes.splice(i, 1);
          continue;
        }
      }
      n.el.style.transform = "translate3d(" + pt.x + "px," + pt.y + "px,0)";
    }
  }

  function loop(now) {
    if (!running) return;
    if (!last) last = now;
    const dt = Math.min(64, now - last);
    last = now;
    if (!paused && !reduced) {
      angle += (dt / ORBIT_MS) * Math.PI * 2;
      tilt += (want - tilt) * 0.06;
    }
    tiltRing();
    place(now);
    tickNotes(now);
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    running = true;
    last = 0;
    raf = requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  function reset() {
    layout();
    build();
    born = reduced ? 0 : performance.now();
    if (reduced) {
      tilt = TILT0;
      place(0);
      word.style.opacity = "1";
      word.style.transform = "translate(-50%, -50%)";
      stop();
    } else {
      word.style.opacity = "0";
      start();
    }
  }

  new IntersectionObserver((ents) => {
    ents.forEach((e) => { e.isIntersecting && !reduced ? start() : stop(); });
  }).observe(root);
  document.addEventListener("visibilitychange", () => { document.hidden ? stop() : !reduced && start(); });
  addEventListener("pointermove", (e) => {
    if (!fine() || reduced) return;
    want = TILT0 + (e.clientX / innerWidth - 0.5) * ((6 * Math.PI) / 180);
  }, { passive: true });
  new ResizeObserver(reset).observe(root);
  reset();
  orbit = { pause() { paused = true; }, resume() { paused = false; }, reset };
}

initNoJs();
prefersReducedMotion();
initHeader();
initCopy();
initToolGroups();
initSeats();
initOrbit();
