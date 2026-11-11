"use strict";
// Wallpaper HUD cyberpunk: tablero de paneles movibles/redimensionables.
// - Paneles de apps: ruleta vertical infinita (da la vuelta sin fin). En modo
//   autoAll (∞) muestran TODAS las apps instaladas y se actualizan solos al
//   instalar / desinstalar (app.py vigila las carpetas de apps); hidden = las
//   que el usuario quito de ese panel.
// - Paneles de info: reloj, sistema, cpu, nucleos, red, bateria, procesos...
// - Panel de terminal (uno solo): la terminal real la pone Python encima
//   (VTE); aqui se dibuja el marco y se le avisa donde va y con que colores.
// El backend (app.py) llama a __init / __updateMetrics / __setAllApps /
// __termToggle y recibe launchApp / requestAllApps / boardUpdate / quitApp /
// termRect / termTheme / termFocus.

const $ = (sel, root = document) => root.querySelector(sel);
const mod = (a, n) => ((a % n) + n) % n;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function post(name, payload) {
  try {
    window.webkit.messageHandlers[name].postMessage(payload);
  } catch (e) {
    console.warn("sin backend:", name, payload);
  }
}

function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html != null) node.innerHTML = html;
  return node;
}

const pad2 = (n) => String(n).padStart(2, "0");

function fmtRate(bps) {
  if (bps == null) return "--";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let i = 0;
  while (bps >= 1024 && i < units.length - 1) { bps /= 1024; i++; }
  return `${bps >= 100 || i === 0 ? Math.round(bps) : bps.toFixed(1)} ${units[i]}`;
}

function fmtDuration(s) {
  if (s == null) return "--";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${pad2(h)}h ${pad2(m)}m` : `${h}h ${pad2(m)}m`;
}

// ============================================================
// Estado
// ============================================================
const state = {
  theme: "red",
  themeBar: DEFAULT_THEME_BAR.slice(), // temas que salen en la barra superior
  panels: [], // {id, kind:"apps"|"info"|"terminal", widget?, title, x, y, w, h, apps?, autoAll?, hidden?, autoroll?, text?, collapsed?}
  apps: {}, // id -> {id, name, icon}
  editMode: false,
  metrics: null,
};
const runtime = new Map(); // panel id -> {el, body, content, ...}
let allApps = null;

const BOARD_VERSION = 6; // 3 = ya trae panel de terminal; 4 = panel de apps automatico (autoAll); 5 = info redondos; 6 = apps en reactor
let saveTimer = null;
function flushSave() {
  clearTimeout(saveTimer);
  saveTimer = null;
  const panels = state.panels.map((p) => {
    const out = { ...p };
    for (const k of ["x", "y", "w", "h"]) out[k] = Math.round(out[k]);
    return out;
  });
  post("boardUpdate", JSON.stringify({ version: BOARD_VERSION, theme: state.theme, themes: state.themeBar, panels }));
}
function saveBoard() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 250);
}
// que un cambio de ultimo momento no se pierda al cerrar/recargar
window.addEventListener("pagehide", () => { if (saveTimer) flushSave(); });

let idSeq = 0;
const newId = () => `p${Date.now().toString(36)}${(idSeq++).toString(36)}`;

// ============================================================
// Colores del tema (los lee el canvas y las graficas)
// ============================================================
const colors = {};
function readColors() {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ["accent", "alt", "warn", "grid", "muted"]) {
    colors[k] = cs.getPropertyValue(`--${k}`).trim();
  }
  colors.accentRgb = cs.getPropertyValue("--accent-rgb").trim();
  colors.altRgb = cs.getPropertyValue("--alt-rgb").trim();
}

const themeById = (id) => THEMES.find((t) => t.id === id);
const currentTheme = () => themeById(state.theme) || THEMES[0];

function hexRgb(hex) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

// Pone los colores del tema como variables CSS en :root (todo el CSS y las
// graficas leen de ahi).
let themeVarNames = [];
function setThemeVars(t) {
  const root = document.documentElement;
  for (const name of themeVarNames) root.style.removeProperty(name);
  const c = t.colors;
  const synth = t.style === "synth";
  const plain = t.style === "plain";
  const glow = t.glow ?? (synth ? 0.5 : 0.22);
  // --accent-rgb tiñe lineas, bordes y fondos translucidos; con `line` el
  // acento solido (textos, rellenos) y las lineas van por separado
  const vars = {
    "--bg": c.bg, "--bg-2": c.bg2 || c.bg,
    "--accent": c.accent, "--accent-rgb": hexRgb(c.line || c.accent),
    "--alt": c.alt, "--alt-rgb": hexRgb(c.alt),
    "--warn": c.warn, "--warn-rgb": hexRgb(c.warn),
    "--text": c.text, "--muted": c.muted,
    "--panel-bg": c.panel, "--floor": c.floor || c.bg2 || c.bg,
    "--glow": `rgba(${hexRgb(c.accent)}, ${glow})`,
    "--grid": plain ? "rgba(0, 0, 0, 0)" : `rgba(${hexRgb(c.accent)}, 0.055)`,
  };
  for (const [k, v] of Object.entries(t.term || {})) vars[`--term-${k}`] = v;
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
  themeVarNames = Object.keys(vars);
  root.dataset.style = synth ? "synth" : "grid";  // "plain" usa el CSS de rejilla y apaga lo demás
  if (plain) root.dataset.plain = "";
  else delete root.dataset.plain;
}

function applyTheme(theme, { flash = false, persist = false } = {}) {
  state.theme = themeById(theme) ? theme : THEMES[0].id;
  setThemeVars(currentTheme());
  renderThemeBar();
  readColors();
  postTermTheme();
  makeGridTile();
  makeFloor();
  runtime.forEach((rt) => rt.content && rt.content.redraw && rt.content.redraw());
  if (flash) {
    document.body.classList.remove("theme-flash");
    void document.body.offsetWidth;
    document.body.classList.add("theme-flash");
    setTimeout(() => document.body.classList.remove("theme-flash"), 450);
  }
  if (persist) saveBoard();
}

// Botones de tema de la barra superior (los elegidos en el selector)
function renderThemeBar() {
  const bar = $("#theme-bar");
  bar.innerHTML = "";
  for (const id of state.themeBar) {
    const t = themeById(id);
    if (!t) continue;
    const chip = el("button", `theme-chip${id === state.theme ? " active" : ""}`);
    chip.dataset.themePick = id;
    chip.title = t.desc;
    const sw = el("i");
    sw.style.background = `linear-gradient(135deg, ${t.colors.accent} 50%, ${t.colors.alt} 50%)`;
    sw.style.boxShadow = `0 0 6px ${t.colors.accent}`;
    chip.append(sw, document.createTextNode(t.name));
    chip.addEventListener("click", () => applyTheme(id, { flash: true, persist: true }));
    bar.appendChild(chip);
  }
}

// ============================================================
// Toast
// ============================================================
let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
}

// ============================================================
// Animador global (un solo requestAnimationFrame para todas las ruletas)
// ============================================================
// Pausa y ahorro (2026-11-07):
//  pausa  = el fondo está tapado (ventana a pantalla completa, maximizada o
//           varias que cubren la pantalla): app.py llama a __pausa(true).
//           Se congela todo: animaciones CSS, giro, cinta, métricas.
//  ahorro = con batería (sin cargar): anillos quietos, fondo a menos fps,
//           el reactor gira más despacio.
const power = { paused: false, saver: false, still: false };
// still = el usuario apagó las animaciones (panel rápido > FONDO > ANIMACIONES):
// todo quieto pero los datos se siguen actualizando.
window.__animaciones = function (on) {
  const still = !on;
  if (still === power.still) return;
  power.still = still;
  document.body.classList.toggle("quieto", still);
  runtime.forEach((rt) => isAppsView(rt.content) && rt.content.syncAuto());
};
window.__pausa = function (on) {
  on = !!on;
  if (on === power.paused) return;
  power.paused = on;
  document.body.classList.toggle("pausa", on);
  if (!on) {  // al volver: ponerse al día de una vez
    tickSecond();
    if (state.metrics) runtime.forEach((rt) => rt.content && rt.content.update && rt.content.update(state.metrics));
  }
  runtime.forEach((rt) => isAppsView(rt.content) && rt.content.syncAuto());
};
function setSaver(on) {
  if (on === power.saver) return;
  power.saver = on;
  document.body.classList.toggle("ahorro", on);
  runtime.forEach((rt) => isAppsView(rt.content) && rt.content.syncAuto());
}

const animators = new Set();
let rafId = null;
let lastFrame = 0;
function wake(a) {
  animators.add(a);
  if (rafId == null) {
    lastFrame = performance.now();
    rafId = requestAnimationFrame(animLoop);
  }
}
function animLoop(t) {
  if (power.paused || power.still) { rafId = null; return; }
  const dt = Math.min(0.08, (t - lastFrame) / 1000);
  lastFrame = t;
  for (const a of animators) if (!a.frame(dt)) animators.delete(a);
  if (!animators.size) { rafId = null; return; }
  // Solo auto-giro (nadie tocando): ~24 fps alcanza y gasta mucho menos.
  // Con rueda/arrastre/giro libre: 60 fps para que se sienta fluido.
  const interactive = [...animators].some((a) => a.mode !== "idle");
  if (interactive) rafId = requestAnimationFrame(animLoop);
  else rafId = setTimeout(() => (rafId = requestAnimationFrame(animLoop)), power.saver ? 66 : 34);
}

// ============================================================
// Ruleta de apps: lista vertical infinita
// ============================================================
const AUTO_SPEED = 0.7; // apps por segundo en modo auto
const SPIN_FRICTION = 0.32; // friccion del giro libre (baja = gira mucho rato)

class Roulette {
  constructor(panel, body) {
    this.panel = panel;
    this.root = el("div", "roulette");
    this.track = el("div", "r-track");
    this.root.append(this.track, el("div", "r-reticle"), el("div", "r-fade top"), el("div", "r-fade bottom"));
    this.tape = el("div", "r-tape");
    this.marker = el("div", "r-marker");
    this.tape.appendChild(this.marker);
    this.root.appendChild(this.tape);
    body.appendChild(this.root);

    this.pos = 0; // posicion (en apps); la app del centro es round(pos) mod n
    this.target = 0;
    this.vel = 0;
    this.mode = "idle"; // idle | ease | spin | drag
    this.hovered = false;
    this.slots = [];
    this.items = [];
    this.h = 0;
    this.ih = 60;
    this.lastCenter = -1;

    this.ro = new ResizeObserver(() => this.layout());
    this.ro.observe(this.root);
    this.bindInput();
  }

  setApps(items) {
    this.items = items;
    this.slots.forEach((s) => (s.idx = -1));
    if (this.empty) { this.empty.remove(); this.empty = null; }
    if (!items.length) {
      this.empty = el("div", "r-empty", '<div class="big-plus">+</div><div>SIN APPS<br>pulsa + para agregar</div>');
      this.empty.querySelector(".big-plus").addEventListener("click", () => openPicker(this.panel.id));
      this.root.appendChild(this.empty);
    }
    this.track.style.display = items.length ? "" : "none";
    this.tape.style.display = items.length ? "" : "none";
    this.render();
    this.syncAuto();
  }

  layout() {
    this.h = this.root.clientHeight;
    const w = this.root.clientWidth;
    this.ih = Math.round(clamp(w * 0.15, 48, 78));
    this.root.style.setProperty("--ih", `${this.ih}px`);
    const half = Math.ceil(this.h / 2 / this.ih) + 1;
    const count = half * 2 + 1;
    this.half = half;
    while (this.slots.length < count) this.slots.push(this.makeSlot());
    while (this.slots.length > count) this.slots.pop().el.remove();
    this.render();
  }

  makeSlot() {
    const node = el("div", "r-item");
    node.innerHTML = '<span class="r-idx"></span><span class="r-ico"></span><span class="r-name"></span><span class="r-go">&gt;&gt;</span><span class="r-remove" title="Quitar de este panel">×</span>';
    this.track.appendChild(node);
    const slot = { el: node, idx: -1 };
    node.querySelector(".r-remove").addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (slot.idx >= 0) removeAppFromPanel(this.panel.id, this.items[slot.idx].id);
    });
    node.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".r-remove")) ev.stopPropagation();
    });
    return slot;
  }

  fillSlot(slot, idx) {
    slot.idx = idx;
    const app = this.items[idx];
    slot.el.querySelector(".r-idx").textContent = pad2(idx + 1);
    slot.el.querySelector(".r-name").textContent = app.name;
    const ico = slot.el.querySelector(".r-ico");
    ico.innerHTML = "";
    if (app.icon) {
      const img = el("img", "r-icon");
      img.src = app.icon;
      img.alt = "";
      img.draggable = false;
      ico.appendChild(img);
    } else {
      ico.appendChild(el("span", "r-letter", app.name.slice(0, 1).toUpperCase()));
    }
    slot.el.title = app.name;
  }

  render() {
    const n = this.items.length;
    if (!n || !this.h) return;
    const center = this.h / 2;
    const base = Math.floor(this.pos);
    const frac = this.pos - base;
    const ih = this.ih;
    this.slots.forEach((slot, i) => {
      const k = i - this.half;
      const rel = k - frac; // distancia al centro en apps
      const idx = mod(base + k, n);
      if (slot.idx !== idx) this.fillSlot(slot, idx);
      const y = center + rel * ih - (ih - 8) / 2;
      const d = clamp((rel * ih) / (this.h / 2), -1.2, 1.2);
      const ad = Math.min(1, Math.abs(d));
      slot.el.style.transform = `translateY(${y.toFixed(1)}px) rotateX(${(-d * 38).toFixed(1)}deg) scale(${(1 - ad * 0.14).toFixed(3)})`;
      slot.el.style.opacity = Math.max(0, 1 - ad * ad * 0.9).toFixed(3);
      slot.el.classList.toggle("is-center", Math.abs(rel) < 0.5);
    });
    const c = mod(Math.round(this.pos), n);
    const tapeH = this.tape.clientHeight - 10;
    const frac01 = mod(this.pos, n) / n;
    this.marker.style.transform = `translateY(${(frac01 * tapeH).toFixed(1)}px)`;
    if (c !== this.lastCenter) {
      this.lastCenter = c;
      const rt = runtime.get(this.panel.id);
      if (rt) rt.footTxt.textContent = `${pad2(c + 1)} / ${pad2(n)} · ${this.items[c].name.toUpperCase()}`;
    }
  }

  // ---- movimiento ----
  get autoActive() {
    return this.panel.autoroll && !this.hovered && this.items.length > 0 && !state.editMode && !power.paused && !power.still;
  }

  syncAuto() {
    if (this.autoActive) wake(this);
    else if (this.mode === "idle" && this.items.length) {
      // se detuvo el auto (hover o boton): acomodar en la app mas cercana
      this.easeTo(Math.round(this.pos + (this.panel.autoroll ? 0.35 : 0)));
    }
  }

  easeTo(target) {
    this.target = target;
    this.mode = "ease";
    wake(this);
  }

  stopSpin() {
    this.vel = 0;
    this.easeTo(Math.round(this.pos));
  }

  goHome() {
    // Avanza hacia adelante hasta la primera app (se ve girar la ruleta).
    const n = this.items.length;
    if (!n) return;
    const t = Math.ceil(this.pos / n - 1e-6) * n;
    this.vel = 0;
    this.easeTo(t);
  }

  frame(dt) {
    const n = this.items.length;
    if (!n) { this.mode = "idle"; return false; }
    let alive = true;
    if (this.mode === "drag") {
      // la posicion la fija el puntero
    } else if (this.mode === "spin") {
      this.pos += this.vel * dt;
      this.vel *= Math.exp(-SPIN_FRICTION * dt);
      if (Math.abs(this.vel) < 1.1) {
        const t = Math.round(this.pos + this.vel * 0.4);
        this.vel = 0;
        this.target = t;
        this.mode = "ease";
      }
    } else if (this.mode === "ease") {
      const k = 1 - Math.exp(-dt * 9);
      this.pos += (this.target - this.pos) * k;
      if (Math.abs(this.target - this.pos) < 0.002) {
        this.pos = this.target;
        this.mode = "idle";
      }
    } else if (this.autoActive) {
      this.pos += AUTO_SPEED * dt;
      this.target = this.pos;
    } else {
      alive = false;
    }
    if (this.mode === "idle" && this.autoActive) alive = true;
    // normalizar para que pos no crezca sin limite
    if (this.mode === "idle" || this.autoActive) {
      const shift = Math.floor(this.pos / n) * n;
      if (shift) { this.pos -= shift; this.target -= shift; }
    }
    this.render();
    return alive || this.mode !== "idle";
  }

  bindInput() {
    let snapTimer = null;
    this.root.addEventListener("wheel", (ev) => {
      if (!this.items.length) return;
      ev.preventDefault();
      if (this.mode === "spin") this.vel = 0;
      let d;
      if (ev.deltaMode === 1) d = ev.deltaY / 3; // lineas
      else if (Math.abs(ev.deltaY) >= 50) d = Math.sign(ev.deltaY); // rueda del mouse: una app por paso
      else d = ev.deltaY / this.ih; // touchpad: continuo
      if (this.mode !== "ease") this.target = this.pos;
      this.target += d;
      this.mode = "ease";
      wake(this);
      clearTimeout(snapTimer);
      snapTimer = setTimeout(() => this.easeTo(Math.round(this.target)), 140);
    }, { passive: false });

    this.root.addEventListener("pointerenter", () => { this.hovered = true; if (this.panel.autoroll) this.syncAuto(); });
    this.root.addEventListener("pointerleave", () => { this.hovered = false; this.syncAuto(); });

    this.root.addEventListener("pointerdown", (ev) => {
      if (state.editMode || ev.button !== 0 || !this.items.length) return;
      ev.stopPropagation();
      const wasSpinning = this.mode === "spin" && Math.abs(this.vel) > 1.5;
      if (this.mode === "spin") this.vel = 0;
      const startY = ev.clientY;
      const startPos = this.pos;
      const slotEl = ev.target.closest(".r-item");
      let dragging = false;
      let samples = [{ t: performance.now(), y: ev.clientY }];

      const onMove = (mv) => {
        if (!dragging && Math.abs(mv.clientY - startY) > 6) {
          dragging = true;
          this.mode = "drag";
          wake(this);
        }
        if (dragging) {
          this.pos = startPos - (mv.clientY - startY) / this.ih;
          samples.push({ t: performance.now(), y: mv.clientY });
          if (samples.length > 6) samples.shift();
        }
      };
      const onUp = (up) => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (dragging) {
          const now = performance.now();
          const recent = samples.filter((s) => now - s.t < 100);
          let v = 0;
          if (recent.length >= 2) {
            const a = recent[0], b = recent[recent.length - 1];
            v = -((b.y - a.y) / Math.max(1, b.t - a.t)) * 1000 / this.ih; // apps/s
          }
          if (Math.abs(v) > 3) {
            this.vel = clamp(v, -45, 45);
            this.mode = "spin";
            wake(this);
          } else {
            this.easeTo(Math.round(this.pos));
          }
          return;
        }
        if (wasSpinning) { this.stopSpin(); return; } // clic = frenar la ruleta
        if (slotEl && !up.target.closest(".r-remove")) {
          const slot = this.slots.find((s) => s.el === slotEl);
          if (slot && slot.idx >= 0) this.launch(slot);
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
  }

  launch(slot) {
    const app = this.items[slot.idx];
    slot.el.classList.remove("launch");
    void slot.el.offsetWidth;
    slot.el.classList.add("launch");
    post("launchApp", app.id);
    toast(`▶ INICIANDO ${app.name.toUpperCase()}`);
  }

  destroy() {
    this.ro.disconnect();
    animators.delete(this);
  }
}

// ============================================================
// Reactor: versión redonda del panel de apps (2026-11-07)
// Núcleo animado en el centro y las apps en un anillo alrededor. La rueda
// del ratón gira el anillo (una app por paso); ▶ lo gira solo. Misma
// interfaz que Roulette (setApps, render, syncAuto, goHome, destroy).
// ============================================================
const REACTOR_MAX = 12; // apps visibles a la vez en el anillo

class Reactor {
  constructor(panel, body) {
    this.panel = panel;
    this.items = [];
    this.offset = 0;      // primera app del anillo
    this.sel = 0;         // app resaltada (la de arriba)
    this.lastCenter = -1;
    this.root = el("div", "reactor");
    this.root.innerHTML = `
      <svg class="rx-bg" viewBox="0 0 200 200">
        <circle cx="100" cy="100" r="98" class="rx-ticks" pathLength="180"/>
        <circle cx="100" cy="100" r="62" class="rx-orbit"/>
        <circle cx="100" cy="100" r="26" class="rx-core-ring"/>
        <circle cx="100" cy="100" r="21" class="rx-core"/>
      </svg>
      <svg class="rx-layer rx-rot slow" viewBox="0 0 200 200"><circle cx="100" cy="100" r="92" class="rx-seg" pathLength="100"/></svg>
      <svg class="rx-layer rx-rot rev" viewBox="0 0 200 200"><circle cx="100" cy="100" r="86" class="rx-arc" pathLength="100"/></svg>
      <svg class="rx-layer rx-rot mid" viewBox="0 0 200 200"><circle cx="100" cy="100" r="40" class="rx-ring" pathLength="100"/></svg>
      <svg class="rx-layer rx-rot fast" viewBox="0 0 200 200"><circle cx="100" cy="100" r="33" class="rx-dash" pathLength="60"/></svg>
      <div class="rx-glow"></div>
      <div class="rx-apps"></div>
      <div class="rx-center"><span class="rx-idx">--</span><span class="rx-name">APPS</span></div>`;
    body.appendChild(this.root);
    this.ring = $(".rx-apps", this.root);
    this.idxEl = $(".rx-idx", this.root);
    this.nameEl = $(".rx-name", this.root);
    this.center = $(".rx-center", this.root);
    this.center.addEventListener("click", () => {
      if (state.editMode) return;
      if (!this.items.length) openPicker(this.panel.id);
      else this.launch(this.sel);
    });
    this.root.addEventListener("wheel", (ev) => {
      if (!this.items.length) return;
      ev.preventDefault();
      this.step(ev.deltaY > 0 ? 1 : -1);
    }, { passive: false });
    this.root.addEventListener("pointerenter", () => { this.hovered = true; });
    this.root.addEventListener("pointerleave", () => { this.hovered = false; this.show(this.sel); });
  }

  setApps(items) {
    this.items = items;
    if (this.sel >= items.length) this.sel = 0;
    this.render();
    this.syncAuto();
  }

  step(d) {
    const n = this.items.length;
    if (!n) return;
    this.sel = mod(this.sel + d, n);
    // si hay más apps que huecos, el anillo avanza con la selección
    if (n > REACTOR_MAX) this.offset = this.sel;
    this.render();
  }

  render() {
    const n = this.items.length;
    this.ring.innerHTML = "";
    this.root.classList.toggle("empty", !n);
    if (!n) { this.idxEl.textContent = "+"; this.nameEl.textContent = "AGREGAR APPS"; return; }
    const k = Math.min(n, REACTOR_MAX);
    for (let i = 0; i < k; i++) {
      const idx = n > REACTOR_MAX ? mod(this.offset + i, n) : i;
      // la seleccionada siempre arriba: el anillo gira para ponerla ahí
      const pos = n > REACTOR_MAX ? i : mod(i - this.sel, k);
      const a = (pos / k) * 2 * Math.PI - Math.PI / 2;
      const app = this.items[idx];
      const b = el("div", "rx-app");
      b.style.left = `${50 + 39 * Math.cos(a)}%`;
      b.style.top = `${50 + 39 * Math.sin(a)}%`;
      b.classList.toggle("sel", idx === this.sel);
      b.title = app.name;
      if (app.icon) { const img = el("img"); img.src = app.icon; img.alt = ""; img.draggable = false; b.appendChild(img); }
      else b.appendChild(el("span", "r-letter", app.name.slice(0, 1).toUpperCase()));
      const x = el("span", "rx-remove", "×");
      x.title = "Quitar de este panel";
      x.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      x.addEventListener("click", (ev) => { ev.stopPropagation(); removeAppFromPanel(this.panel.id, app.id); });
      b.appendChild(x);
      b.addEventListener("pointerenter", () => this.show(idx));
      b.addEventListener("click", () => { if (!state.editMode) this.launch(idx); });
      this.ring.appendChild(b);
    }
    this.show(this.sel);
  }

  show(idx) {
    const app = this.items[idx];
    if (!app) return;
    this.idxEl.textContent = `${pad2(idx + 1)} / ${pad2(this.items.length)}`;
    this.nameEl.textContent = app.name.toUpperCase();
    this.lastCenter = idx;
  }

  launch(idx) {
    const app = this.items[idx];
    if (!app) return;
    this.sel = idx;
    this.render();
    this.root.classList.remove("launch");
    void this.root.offsetWidth;
    this.root.classList.add("launch");
    post("launchApp", app.id);
    toast(`▶ INICIANDO ${app.name.toUpperCase()}`);
  }

  syncAuto() {
    clearInterval(this.timer);
    this.timer = null;
    if (this.panel.autoroll && !state.editMode && !power.paused && !power.still && this.items.length > 1)
      this.timer = setInterval(() => { if (!this.hovered) this.step(1); }, power.saver ? 5000 : 2500);
  }

  goHome() { this.sel = 0; this.offset = 0; this.render(); }
  destroy() { clearInterval(this.timer); }
}

// ============================================================
// Graficas (sparklines) en canvas
// ============================================================
class Spark {
  constructor(canvas, series) {
    this.canvas = canvas;
    this.series = series; // [{data: [], color: () => css, fill: bool}]
    this.max = 100;
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(canvas);
  }
  draw() {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // rejilla
    ctx.strokeStyle = `rgba(${colors.accentRgb}, 0.12)`;
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = Math.round((h * i) / 4) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    const max = Math.max(this.max, 1e-6);
    for (const s of this.series) {
      const data = s.data;
      if (data.length < 2) continue;
      const step = w / (HIST_LEN - 1);
      const x0 = w - (data.length - 1) * step;
      ctx.beginPath();
      data.forEach((v, i) => {
        const x = x0 + i * step;
        const y = h - (clamp(v, 0, max) / max) * (h - 3) - 1;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      const col = s.rgb();
      ctx.strokeStyle = `rgb(${col})`;
      ctx.lineWidth = 1.6;
      ctx.shadowColor = `rgb(${col})`;
      ctx.shadowBlur = 6;
      ctx.stroke();
      ctx.shadowBlur = 0;
      if (s.fill) {
        ctx.lineTo(w, h); ctx.lineTo(x0, h); ctx.closePath();
        const g = ctx.createLinearGradient(0, 0, 0, h);
        g.addColorStop(0, `rgba(${col}, 0.35)`);
        g.addColorStop(1, `rgba(${col}, 0)`);
        ctx.fillStyle = g;
        ctx.fill();
      }
    }
  }
  destroy() { this.ro.disconnect(); }
}

const HIST_LEN = 90;
const hist = { cpu: [], up: [], down: [] };
function pushHist(arr, v) {
  arr.push(v == null ? 0 : v);
  if (arr.length > HIST_LEN) arr.shift();
}

function stateFor(value, warn, crit, invert = false) {
  if (value == null) return "normal";
  if (invert) return value <= crit ? "critical" : value <= warn ? "warning" : "normal";
  return value >= crit ? "critical" : value >= warn ? "warning" : "normal";
}

function setState(node, s) {
  if (s === "normal") node.removeAttribute("data-state");
  else node.setAttribute("data-state", s);
}

// ============================================================
// Widgets de info
// ============================================================
const WIDGETS = {
  clock: {
    name: "Reloj", desc: "Hora, segundos y fecha", size: [400, 170],
    build(root) {
      const w = el("div", "w w-clock", '<div class="time"><span class="hm">--:--</span><span class="ss">--</span></div><div class="date">--</div><div class="segbar"><i></i></div>');
      root.appendChild(w);
      const hm = $(".hm", w), ss = $(".ss", w), date = $(".date", w), bar = $(".segbar > i", w);
      return {
        tick() {
          const now = new Date();
          hm.textContent = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
          ss.textContent = pad2(now.getSeconds());
          date.textContent = now.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
          bar.style.width = `${((now.getSeconds() + 1) / 60) * 100}%`;
        },
      };
    },
  },

  system: {
    name: "Sistema", desc: "CPU, RAM, disco, temperatura y swap", size: [400, 230],
    build(root) {
      const rows = [
        ["cpu_percent", "CPU", 75, 90, 100, "%"],
        ["ram_used_percent", "RAM", 80, 92, 100, "%"],
        ["disk_used_percent", "DISCO", 85, 95, 100, "%"],
        ["temp_c", "TEMP", 70, 85, 100, "°C"],
        ["swap_used_percent", "SWAP", 50, 80, 100, "%"],
      ];
      const w = el("div", "w w-sys");
      const refs = rows.map(([key, label, warn, crit, max, unit]) => {
        const r = el("div", "sys-row", `<span class="w-label">${label}</span><div class="segbar"><i></i></div><span class="w-val">--</span>`);
        w.appendChild(r);
        return { key, warn, crit, max, unit, row: r, bar: $(".segbar > i", r), val: $(".w-val", r) };
      });
      root.appendChild(w);
      return {
        update(m) {
          for (const r of refs) {
            const v = m[r.key];
            if (v == null) { r.val.textContent = "--"; continue; }
            r.bar.style.width = `${clamp(v / r.max, 0, 1) * 100}%`;
            r.val.textContent = `${Math.round(v)}${r.unit}`;
            setState(r.row, stateFor(v, r.warn, r.crit));
          }
        },
      };
    },
  },

  cpu: {
    name: "Gráfica CPU", desc: "Uso de CPU en vivo con historial", size: [400, 200],
    build(root) {
      const w = el("div", "w w-graph", '<div class="top"><div class="big">--<small>%</small></div><div class="side"><span class="w-label">PROCESADOR</span><span class="w-val freq">-- MHz</span><span class="w-label load">CARGA --</span></div></div><canvas></canvas>');
      root.appendChild(w);
      const spark = new Spark($("canvas", w), [{ data: hist.cpu, rgb: () => colors.accentRgb, fill: true }]);
      const big = $(".big", w), freq = $(".freq", w), load = $(".load", w);
      return {
        update(m) {
          big.innerHTML = `${m.cpu_percent == null ? "--" : Math.round(m.cpu_percent)}<small>%</small>`;
          freq.textContent = m.cpu_freq_mhz ? `${m.cpu_freq_mhz} MHz` : "-- MHz";
          load.textContent = m.load_avg ? `CARGA ${m.load_avg.join(" ")}` : "CARGA --";
          spark.draw();
        },
        redraw() { spark.draw(); },
        destroy() { spark.destroy(); },
      };
    },
  },

  cores: {
    name: "Núcleos", desc: "Barras de uso por cada núcleo", size: [400, 190],
    build(root) {
      const w = el("div", "w w-cores", '<span class="w-label">NÚCLEOS · <span class="w-val n">--</span></span><div class="cores"></div>');
      root.appendChild(w);
      const box = $(".cores", w), nEl = $(".n", w);
      let bars = [];
      return {
        update(m) {
          const cores = m.cpu_cores || [];
          if (bars.length !== cores.length) {
            box.innerHTML = "";
            bars = cores.map((_, i) => {
              const c = el("div", "core", `<div class="col"><i></i></div><span>${pad2(i)}</span>`);
              box.appendChild(c);
              return $(".col > i", c);
            });
            nEl.textContent = String(cores.length);
          }
          cores.forEach((v, i) => (bars[i].style.height = `${clamp(v, 0, 100)}%`));
        },
      };
    },
  },

  network: {
    name: "Red", desc: "Velocidad de subida y bajada", size: [400, 200],
    build(root) {
      const w = el("div", "w w-graph w-net", '<div class="rates"><div class="rate down"><span class="w-label">▼ BAJADA</span><span class="num">--</span></div><div class="rate up"><span class="w-label">▲ SUBIDA</span><span class="num">--</span></div></div><canvas></canvas>');
      root.appendChild(w);
      const spark = new Spark($("canvas", w), [
        { data: hist.down, rgb: () => colors.altRgb, fill: true },
        { data: hist.up, rgb: () => colors.accentRgb, fill: false },
      ]);
      const down = $(".down .num", w), up = $(".up .num", w);
      return {
        update(m) {
          down.textContent = fmtRate(m.net_down_bps);
          up.textContent = fmtRate(m.net_up_bps);
          spark.max = Math.max(32 * 1024, ...hist.down, ...hist.up) * 1.15;
          spark.draw();
        },
        redraw() { spark.draw(); },
        destroy() { spark.destroy(); },
      };
    },
  },

  battery: {
    name: "Batería", desc: "Carga, estado y tiempo restante", size: [400, 150],
    build(root) {
      const w = el("div", "w w-batt", '<div class="row"><div class="big">--%</div><div class="cells"></div></div><div class="status">--</div>');
      root.appendChild(w);
      const cells = $(".cells", w);
      const cellEls = Array.from({ length: 10 }, () => cells.appendChild(el("div", "cell")));
      const big = $(".big", w), status = $(".status", w);
      return {
        update(m) {
          const p = m.battery_percent;
          if (p == null) { big.textContent = "N/A"; status.textContent = "SIN BATERÍA DETECTADA"; return; }
          big.textContent = `${Math.round(p)}%`;
          const on = Math.ceil(p / 10);
          cellEls.forEach((c, i) => c.classList.toggle("on", i < on));
          w.classList.toggle("charging", !!m.battery_charging);
          setState(w, stateFor(p, 20, 10, true));
          if (m.battery_charging) status.textContent = p >= 99 ? "⚡ CONECTADA · LLENA" : "⚡ CARGANDO";
          else status.textContent = m.battery_secs_left ? `DESCARGANDO · QUEDAN ${fmtDuration(m.battery_secs_left)}` : "DESCARGANDO";
        },
      };
    },
  },

  processes: {
    name: "Procesos", desc: "Los que más CPU están usando", size: [400, 230],
    build(root) {
      const w = el("div", "w w-procs", '<div class="proc-row head"><span>PROCESO</span><span style="text-align:right">CPU</span><span style="text-align:right">RAM</span></div>');
      root.appendChild(w);
      const rows = Array.from({ length: 6 }, () => {
        const r = el("div", "proc-row", '<span class="pname">--</span><span class="pcpu">--</span><span class="pmem">--</span>');
        w.appendChild(r);
        return r;
      });
      return {
        update(m) {
          const ps = m.processes || [];
          rows.forEach((r, i) => {
            const p = ps[i];
            r.children[0].textContent = p ? p.name : "--";
            r.children[1].textContent = p ? `${p.cpu.toFixed(1)}%` : "";
            r.children[2].textContent = p ? `${p.mem.toFixed(1)}%` : "";
          });
        },
      };
    },
  },

  host: {
    name: "Equipo", desc: "Usuario, host, kernel, uptime y RAM", size: [400, 170],
    build(root) {
      const keys = [["user", "USUARIO"], ["hostname", "HOST"], ["kernel", "KERNEL"], ["uptime", "ENCENDIDO"], ["ram", "MEMORIA"]];
      const w = el("div", "w w-host");
      const vals = {};
      for (const [k, label] of keys) {
        const r = el("div", "kv", `<span class="w-label">${label}</span><span class="w-val">--</span>`);
        w.appendChild(r);
        vals[k] = $(".w-val", r);
      }
      root.appendChild(w);
      return {
        update(m) {
          vals.user.textContent = m.user || "--";
          vals.hostname.textContent = m.hostname || "--";
          vals.kernel.textContent = m.kernel || "--";
          vals.uptime.textContent = fmtDuration(m.uptime_s);
          vals.ram.textContent = m.ram_total_gb ? `${m.ram_used_gb} / ${m.ram_total_gb} GB` : "--";
        },
      };
    },
  },

  note: {
    name: "Nota", desc: "Un bloc de notas que se guarda solo", size: [360, 200],
    build(root, panel) {
      const w = el("div", "w w-note", '<textarea placeholder="> escribe algo..." spellcheck="false"></textarea>');
      root.appendChild(w);
      const ta = $("textarea", w);
      ta.value = panel.text || "";
      ta.addEventListener("input", () => { panel.text = ta.value; saveBoard(); });
      ta.addEventListener("pointerdown", (ev) => { if (!state.editMode) ev.stopPropagation(); });
      ta.addEventListener("keydown", (ev) => ev.stopPropagation()); // que E/Esc no activen atajos
      return {};
    },
  },
};

// ============================================================
// Widgets redondos (HUD holográfico, 2026-11-07)
// Los paneles de info son redondos salvo que panel.shape === "rect".
// Se dibujan en un SVG de 200×200 que escala con el panel (siempre cuadrado).
// ============================================================
const SVGNS = "http://www.w3.org/2000/svg";
const ROUND_MIN = 140;   // lado mínimo de un panel redondo
const ROUND_NEW = 220;   // lado de uno nuevo
const ORB = 64;          // tamaño minimizado
const isRound = (p) => (p.kind === "info" || p.kind === "apps") && p.shape !== "rect";
const ROUND_MIN_APPS = 300; // el reactor necesita sitio para el anillo de apps
const roundMin = (p) => (p.kind === "apps" ? ROUND_MIN_APPS : ROUND_MIN);
const isAppsView = (c) => c instanceof Roulette || c instanceof Reactor;

function sv(parent, tag, attrs = {}, text = null) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  parent.appendChild(n);
  return n;
}

// Lienzo + decoración común: aro exterior, marcas, un arco que gira despacio.
function ringBase(root, cls) {
  const w = el("div", `w w-ring ${cls}`);
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("viewBox", "0 0 200 200");
  w.appendChild(s);
  root.appendChild(w);
  sv(s, "circle", { cx: 100, cy: 100, r: 97, class: "rg-outer" });
  sv(s, "circle", { cx: 100, cy: 100, r: 91, class: "rg-ticks", pathLength: 120 });
  // Optimización (2026-11-07): lo que gira va en un <svg> aparte = capa propia
  // que el compositor rota sin volver a pintar el widget (ni sus brillos).
  const spin = document.createElementNS(SVGNS, "svg");
  spin.setAttribute("viewBox", "0 0 200 200");
  spin.setAttribute("class", "rg-layer rg-spin");
  sv(spin, "circle", { cx: 100, cy: 100, r: 94, class: "rg-sweep", pathLength: 100 });
  w.appendChild(spin);
  return { w, s };
}

// Arco de valor: span grados empezando en start (0 = derecha, sentido horario).
function gauge(s, r, width, cls = "", start = 135, span = 270) {
  const g = sv(s, "g", { transform: `rotate(${start} 100 100)` });
  const L = (span / 360) * 100;
  sv(g, "circle", { cx: 100, cy: 100, r, class: "rg-track", "stroke-width": width, pathLength: 100, "stroke-dasharray": `${L} 100` });
  const v = sv(g, "circle", { cx: 100, cy: 100, r, class: `rg-val ${cls}`, "stroke-width": width, pathLength: 100, "stroke-dasharray": `0 100` });
  return {
    set(f) { v.setAttribute("stroke-dasharray", `${clamp(f || 0, 0, 1) * L} 100`); },
    state(st) { if (st === "normal") v.removeAttribute("data-state"); else v.setAttribute("data-state", st); },
  };
}

const txt = (s, x, y, size, cls, t = "--") => sv(s, "text", { x, y, "font-size": size, class: cls, "text-anchor": "middle", "dominant-baseline": "middle" }, t);

const RINGS = {
  clock(root) {
    const { s } = ringBase(root, "r-clock");
    const sec = gauge(s, 80, 4, "alt", -90, 360);
    const hm = txt(s, 100, 92, 40, "rg-big"), ss = txt(s, 100, 122, 13, "rg-alt");
    const wd = txt(s, 100, 58, 11, "rg-lbl"), dt = txt(s, 100, 142, 11, "rg-lbl");
    return {
      tick() {
        const n = new Date();
        hm.textContent = `${pad2(n.getHours())}:${pad2(n.getMinutes())}`;
        ss.textContent = pad2(n.getSeconds());
        wd.textContent = n.toLocaleDateString("es-ES", { weekday: "long" }).toUpperCase();
        dt.textContent = n.toLocaleDateString("es-ES", { day: "numeric", month: "long" }).toUpperCase();
        sec.set((n.getSeconds() + 1) / 60);
      },
    };
  },

  system(root) {
    const { s } = ringBase(root, "r-sys");
    const rows = [
      ["cpu_percent", "CPU", 75, 90, 80], ["ram_used_percent", "RAM", 80, 92, 69],
      ["disk_used_percent", "DSK", 85, 95, 58], ["swap_used_percent", "SWP", 50, 80, 47],
    ];
    const refs = rows.map(([key, lbl, warn, crit, r], i) => {
      const g = gauge(s, r, 7, i % 2 ? "alt" : "");
      sv(s, "text", { x: 100 - 6, y: 100 + r, "font-size": 7, class: "rg-lbl", "text-anchor": "end", "dominant-baseline": "middle" }, lbl);
      return { key, warn, crit, g, v: txt(s, 100, 76 + i * 13, 10, "rg-val-t") };
    });
    refs.forEach((r, i) => (r.v.textContent = `${rows[i][1]} --`));
    const temp = txt(s, 140, 168, 10, "rg-alt", "-- °C");
    return {
      update(m) {
        refs.forEach((r, i) => {
          const v = m[r.key];
          r.g.set(v == null ? 0 : v / 100);
          r.g.state(stateFor(v, r.warn, r.crit));
          r.v.textContent = `${rows[i][1]} ${v == null ? "--" : Math.round(v) + "%"}`;
        });
        temp.textContent = m.temp_c == null ? "-- °C" : `${Math.round(m.temp_c)} °C`;
      },
    };
  },

  cpu(root) {
    const { s } = ringBase(root, "r-cpu");
    const g = gauge(s, 78, 11);
    const load = gauge(s, 62, 3, "alt");
    const big = txt(s, 100, 96, 38, "rg-big"), lbl = txt(s, 100, 64, 10, "rg-lbl", "CPU");
    const freq = txt(s, 100, 126, 10, "rg-alt"), la = txt(s, 100, 168, 9, "rg-lbl");
    void lbl;
    return {
      update(m) {
        const v = m.cpu_percent;
        g.set(v == null ? 0 : v / 100);
        g.state(stateFor(v, 75, 90));
        big.textContent = v == null ? "--%" : `${Math.round(v)}%`;
        freq.textContent = m.cpu_freq_mhz ? `${m.cpu_freq_mhz} MHz` : "-- MHz";
        const n = (m.cpu_cores || []).length || 1;
        load.set(m.load_avg ? m.load_avg[0] / n : 0);
        la.textContent = m.load_avg ? `CARGA ${m.load_avg[0]}` : "CARGA --";
      },
    };
  },

  cores(root) {
    const { s } = ringBase(root, "r-cores");
    sv(s, "circle", { cx: 100, cy: 100, r: 38, class: "rg-track", "stroke-width": 1 });
    const n = txt(s, 100, 94, 22, "rg-big"), lbl = txt(s, 100, 114, 8, "rg-lbl", "NÚCLEOS");
    void lbl;
    const g = sv(s, "g");
    let bars = [];
    return {
      update(m) {
        const cores = m.cpu_cores || [];
        if (bars.length !== cores.length) {
          g.innerHTML = "";
          bars = cores.map((_, i) => {
            const a = (i / cores.length) * 2 * Math.PI - Math.PI / 2;
            const tr = `rotate(${(a * 180) / Math.PI} 100 100)`;
            sv(g, "line", { x1: 144, y1: 100, x2: 186, y2: 100, class: "rg-track", "stroke-width": 7, transform: tr });
            return sv(g, "line", { x1: 144, y1: 100, x2: 144, y2: 100, class: "rg-val", "stroke-width": 7, transform: tr });
          });
        }
        cores.forEach((v, i) => {
          bars[i].setAttribute("x2", 144 + (clamp(v, 0, 100) / 100) * 42);
          if (v >= 90) bars[i].setAttribute("data-state", "critical"); else bars[i].removeAttribute("data-state");
        });
        const avg = cores.length ? cores.reduce((a, b) => a + b, 0) / cores.length : null;
        n.textContent = avg == null ? "--" : `${Math.round(avg)}%`;
      },
    };
  },

  network(root) {
    const { s } = ringBase(root, "r-net");
    const down = gauge(s, 80, 8, "alt"), up = gauge(s, 67, 8);
    txt(s, 100, 72, 9, "rg-lbl", "▼ BAJADA");
    const d = txt(s, 100, 88, 15, "rg-big-s");
    txt(s, 100, 110, 9, "rg-lbl", "▲ SUBIDA");
    const u = txt(s, 100, 126, 15, "rg-big-s");
    return {
      update(m) {
        const max = Math.max(32 * 1024, ...hist.down, ...hist.up) * 1.15;
        down.set((m.net_down_bps || 0) / max);
        up.set((m.net_up_bps || 0) / max);
        d.textContent = fmtRate(m.net_down_bps);
        u.textContent = fmtRate(m.net_up_bps);
      },
    };
  },

  battery(root) {
    const { s, w } = ringBase(root, "r-batt");
    const g = gauge(s, 78, 13);
    sv(s, "circle", { cx: 100, cy: 100, r: 78, class: "rg-cuts", "stroke-width": 15, pathLength: 100, transform: "rotate(135 100 100)" });
    const big = txt(s, 100, 96, 34, "rg-big"), st = txt(s, 100, 126, 9, "rg-lbl"), lbl = txt(s, 100, 66, 10, "rg-lbl", "ENERGÍA");
    void lbl;
    return {
      update(m) {
        const p = m.battery_percent;
        if (p == null) { big.textContent = "N/A"; st.textContent = "SIN BATERÍA"; g.set(0); return; }
        g.set(p / 100);
        g.state(stateFor(p, 20, 10, true));
        big.textContent = `${Math.round(p)}%`;
        w.classList.toggle("charging", !!m.battery_charging);
        st.textContent = m.battery_charging ? (p >= 99 ? "⚡ LLENA" : "⚡ CARGANDO")
          : m.battery_secs_left ? `QUEDAN ${fmtDuration(m.battery_secs_left)}` : "DESCARGANDO";
      },
    };
  },

  processes(root) {
    const { s } = ringBase(root, "r-procs");
    const g = gauge(s, 80, 6, "alt");
    txt(s, 100, 52, 9, "rg-lbl", "PROCESOS");
    const rows = Array.from({ length: 5 }, (_, i) => txt(s, 100, 74 + i * 15, 10, i ? "rg-val-t" : "rg-alt"));
    return {
      update(m) {
        const ps = m.processes || [];
        g.set(ps[0] ? ps[0].cpu / 100 : 0);
        rows.forEach((r, i) => {
          const p = ps[i];
          r.textContent = p ? `${p.name.slice(0, 12)} ${p.cpu.toFixed(0)}%` : "";
        });
      },
    };
  },

  host(root) {
    const { s } = ringBase(root, "r-host");
    const g = gauge(s, 80, 6);
    const who = txt(s, 100, 70, 11, "rg-alt"), host = txt(s, 100, 88, 15, "rg-big-s");
    const kern = txt(s, 100, 106, 8, "rg-lbl"), up = txt(s, 100, 124, 10, "rg-val-t"), ram = txt(s, 100, 140, 9, "rg-lbl");
    return {
      update(m) {
        who.textContent = (m.user || "--").toUpperCase();
        host.textContent = m.hostname || "--";
        kern.textContent = (m.kernel || "--").slice(0, 24);
        up.textContent = `↑ ${fmtDuration(m.uptime_s)}`;
        ram.textContent = m.ram_total_gb ? `RAM ${m.ram_used_gb} / ${m.ram_total_gb} GB` : "RAM --";
        g.set(m.ram_total_gb ? m.ram_used_gb / m.ram_total_gb : 0);
      },
    };
  },

  note(root, panel) {
    const { w } = ringBase(root, "r-note");
    const c = WIDGETS.note.build(w, panel);
    return c;
  },
};

// Etiqueta corta del panel minimizado (orbe)
const ORB_LABEL = { apps: "APP", clock: "⌚", system: "SYS", cpu: "CPU", cores: "CPU", network: "NET", battery: "BAT", processes: "PRC", host: "PC", note: "✎" };

// ============================================================
// Paneles
// ============================================================
const MIN_SIZE = { apps: [260, 220], info: [230, 110], terminal: [320, 160] };

function panelApps(panel) {
  if (panel.autoAll && allApps !== null) {
    const hid = new Set(panel.hidden || []);
    return allApps.filter((a) => !hid.has(a.id));
  }
  return (panel.apps || []).map((id) => state.apps[id]).filter(Boolean);
}

// esta la app en el panel? (en autoAll: si no esta oculta)
function inPanel(panel, appId) {
  return panel.autoAll ? !(panel.hidden || []).includes(appId) : (panel.apps || []).includes(appId);
}

function toggleInPanel(panel, appId) {
  const key = panel.autoAll ? "hidden" : "apps";
  const list = panel[key] || (panel[key] = []);
  if (list.includes(appId)) panel[key] = list.filter((id) => id !== appId);
  else list.push(appId);
}

function codeFor(panel) {
  const i = state.panels.filter((p) => p.kind === panel.kind).indexOf(panel) + 1;
  const prefix = { apps: "APP", info: "SYS", terminal: "TTY" }[panel.kind] || "SYS";
  return `${prefix}-${pad2(i)}`;
}

function createPanel(panel) {
  const node = el("div", `panel kind-${panel.kind}`);
  node.dataset.id = panel.id;
  node.innerHTML = `
    <div class="panel-glow"><div class="panel-frame"></div></div>
    <div class="panel-inner">
      <div class="panel-head">
        <div class="panel-tab"><span class="panel-code"></span><span class="panel-title"></span></div>
        <div class="panel-stripes"></div>
        <div class="panel-tools"></div>
      </div>
      <div class="panel-body"></div>
      <div class="panel-foot"><span class="foot-txt"></span><span class="foot-bar"></span></div>
    </div>
    <div class="orb-label"></div>
    <div class="size-badge"></div>`;
  for (const dir of ["n", "s", "e", "w", "ne", "nw", "se", "sw"]) {
    const h = el("div", `rh ${dir}`);
    h.dataset.dir = dir;
    node.appendChild(h);
  }
  $("#board").appendChild(node);

  const rt = {
    el: node,
    body: $(".panel-body", node),
    title: $(".panel-title", node),
    tools: $(".panel-tools", node),
    footTxt: $(".foot-txt", node),
    content: null,
  };
  runtime.set(panel.id, rt);
  rt.title.textContent = panel.title;
  applyGeometry(panel);
  buildTools(panel, rt);
  buildBody(panel, rt);
  bindPanelEditing(panel, rt);
  if (panel.kind === "terminal") bindTerminalPanel(panel, rt);
  return rt;
}

function refreshCodes() {
  for (const p of state.panels) {
    const rt = runtime.get(p.id);
    if (rt) $(".panel-code", rt.el).textContent = codeFor(p);
  }
}

function applyGeometry(panel) {
  const rt = runtime.get(panel.id);
  if (!rt) return;
  // Terminal plegada: solo queda la barra de titulo, pegada al borde de
  // abajo del panel (se despliega hacia arriba, tipo consola de juego).
  // En modo edicion se muestra entera para poder moverla y darle tamaño.
  const folded = panel.kind === "terminal" && panel.collapsed && !state.editMode;
  const h = folded ? TERM_FOLDED_H : panel.h;
  const y = folded ? panel.y + panel.h - TERM_FOLDED_H : panel.y;
  rt.el.classList.toggle("folded", folded);
  const round = isRound(panel), min = round && !!panel.min;
  rt.el.classList.toggle("round", round);
  rt.el.classList.toggle("min", min);
  if (min) {
    // orbe pequeño en el centro de donde estaba el panel
    Object.assign(rt.el.style, { left: `${panel.x + (panel.w - ORB) / 2}px`, top: `${panel.y + (panel.h - ORB) / 2}px`, width: `${ORB}px`, height: `${ORB}px` });
    return;
  }
  Object.assign(rt.el.style, { left: `${panel.x}px`, top: `${y}px`, width: `${panel.w}px`, height: `${h}px` });
  if (panel.kind === "terminal") syncTerm();
}

function tool(label, title, cls, onClick) {
  const b = el("button", `ptool ${cls || ""}`, label);
  b.title = title;
  b.addEventListener("pointerdown", (ev) => ev.stopPropagation());
  b.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(b); });
  return b;
}

function buildTools(panel, rt) {
  rt.tools.innerHTML = "";
  if (panel.kind === "apps") {
    const auto = tool(panel.autoroll ? "■" : "▶", "", "", (b) => {
      panel.autoroll = !panel.autoroll;
      syncAutoButton(panel, b);
      rt.content.syncAuto();
      saveBoard();
    });
    syncAutoButton(panel, auto);
    rt.tools.append(
      auto,
      tool("⤒", "Volver a la primera app", "", () => rt.content.goHome()),
      syncAllButton(panel, tool("∞", "", "", (b) => {
        panel.autoAll = !panel.autoAll;
        if (panel.autoAll && !Array.isArray(panel.hidden)) panel.hidden = [];
        syncAllButton(panel, b);
        if (panel.autoAll && allApps === null) post("requestAllApps", "");
        refreshPanelApps(panel);
        saveBoard();
        toast(panel.autoAll ? "∞ TODAS LAS APPS · SE ACTUALIZA SOLO" : "✎ SOLO LAS APPS ELEGIDAS");
      })),
      tool("+", "Agregar apps a este panel", "", () => openPicker(panel.id))
    );
  }
  if (panel.kind === "terminal") {
    const fold = tool("", "", "term-fold normal-only", () => toggleTerminal());
    rt.foldBtn = fold;
    syncFoldButton(panel, rt);
    rt.tools.append(fold);
  }
  rt.tools.append(tool("✎", "Renombrar panel", "edit-only", () => startRename(panel, rt)));
  if (panel.kind === "info") {
    rt.tools.append(tool("⚙", "Cambiar qué muestra este panel", "edit-only", () => openInfoMenu(panel.id)));
    rt.tools.append(tool(isRound(panel) ? "▭" : "◯", isRound(panel) ? "Hacerlo rectangular" : "Hacerlo redondo", "edit-only", () => toggleShape(panel)));
  }
  if (panel.kind === "apps") {
    rt.tools.append(tool(isRound(panel) ? "▭" : "◯", isRound(panel) ? "Ruleta rectangular" : "Reactor redondo", "edit-only", () => toggleShape(panel)));
  }
  if (isRound(panel)) rt.tools.append(tool("–", "Minimizar (clic en el orbe para volver)", "", () => setMin(panel, true)));
  rt.tools.append(
    tool("✕", "Quitar este panel", "edit-only", (b) => {
      if (b.classList.contains("armed")) { removePanel(panel.id); return; }
      b.classList.add("armed");
      b.textContent = "¿QUITAR?";
      setTimeout(() => { if (b.isConnected) { b.classList.remove("armed"); b.textContent = "✕"; } }, 2500);
    })
  );
}

function syncAllButton(panel, b) {
  b.classList.toggle("on", !!panel.autoAll);
  b.title = panel.autoAll ? "Todas las apps instaladas, se actualiza sola (clic = solo las elegidas)"
                          : "Solo las apps elegidas (clic = todas las instaladas, automático)";
  return b;
}

function syncAutoButton(panel, b) {
  b.textContent = panel.autoroll ? "■" : "▶";
  b.classList.toggle("on", !!panel.autoroll);
  b.title = panel.autoroll ? "Detener el giro automático" : "Girar automáticamente sin fin";
}

function buildBody(panel, rt) {
  if (rt.content && rt.content.destroy) rt.content.destroy();
  rt.body.innerHTML = "";
  if (panel.kind === "apps") {
    rt.content = isRound(panel) ? new Reactor(panel, rt.body) : new Roulette(panel, rt.body);
    $(".orb-label", rt.el).textContent = ORB_LABEL.apps;
    rt.content.setApps(panelApps(panel));
    rt.footTxt.textContent = "";
    rt.content.lastCenter = -1;
    rt.content.render();
  } else if (panel.kind === "terminal") {
    rt.content = buildTerminalBody(rt.body);
    rt.footTxt.textContent = "TTY//BASH · F12 PLEGAR · CTRL+SHIFT+C/V COPIAR/PEGAR";
  } else {
    const def = WIDGETS[panel.widget] || WIDGETS.clock;
    const ring = isRound(panel) && (RINGS[panel.widget] || RINGS.clock);
    rt.content = ring ? ring(rt.body, panel) : def.build(rt.body, panel);
    $(".orb-label", rt.el).textContent = ORB_LABEL[panel.widget] || "◉";
    rt.footTxt.textContent = `MOD//${(panel.widget || "clock").toUpperCase()} · ONLINE`;
    if (rt.content.tick) rt.content.tick();
    if (state.metrics && rt.content.update) rt.content.update(state.metrics);
  }
}

function startRename(panel, rt) {
  const t = rt.title;
  t.contentEditable = "true";
  t.focus();
  const range = document.createRange();
  range.selectNodeContents(t);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const finish = (commit) => {
    t.contentEditable = "false";
    t.removeEventListener("keydown", onKey);
    t.removeEventListener("blur", onBlur);
    const v = t.textContent.trim().slice(0, 40);
    if (commit && v) { panel.title = v; saveBoard(); }
    t.textContent = panel.title;
  };
  const onKey = (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  };
  const onBlur = () => finish(true);
  t.addEventListener("keydown", onKey);
  t.addEventListener("blur", onBlur);
  t.addEventListener("pointerdown", (ev) => ev.stopPropagation(), { once: true });
}

function bringToFront(panel) {
  const i = state.panels.indexOf(panel);
  if (i === state.panels.length - 1) return;
  state.panels.splice(i, 1);
  state.panels.push(panel);
  $("#board").appendChild(runtime.get(panel.id).el);
}

const SNAP = 4; // rejilla invisible a la que se ajustan los paneles
const GUIDE_T = 4; // distancia (px) a la que una guia "imanta" el panel

// ---- Guias inteligentes del modo edicion ----
// Al mover: los bordes y el centro del panel se imantan a los bordes y
// centros de los demas paneles y al centro de la pantalla. Al cambiar el
// tamaño: el borde que se estira se imanta a los bordes de los demas, y si
// no hay borde cerca, el ancho/alto se imanta a la medida de otro panel.
const edgesOf = (p) => ({ l: p.x, cx: p.x + p.w / 2, r: p.x + p.w, t: p.y, cy: p.y + p.h / 2, b: p.y + p.h });

// ---- Separacion igual entre paneles ----
// En el eje x cuentan como vecinos los paneles que comparten franja vertical
// (y al reves en el eje y).
const AX = {
  x: { lo: "l", hi: "r", c: "cx", olo: "t", ohi: "b" },
  y: { lo: "t", hi: "b", c: "cy", olo: "l", ohi: "r" },
};
const overlaps = (a, b, ax) => Math.min(a[AX[ax].ohi], b[AX[ax].ohi]) - Math.max(a[AX[ax].olo], b[AX[ax].olo]) > 0;

// huecos entre cada panel y su vecino siguiente en ese eje
function neighborGaps(list, ax) {
  const { lo, hi } = AX[ax];
  const out = [];
  for (const a of list) {
    let best = null;
    for (const b of list) {
      if (a === b || !overlaps(a, b, ax)) continue;
      const g = b[lo] - a[hi];
      if (g > 0 && (!best || g < best.g)) best = { a, b, g };
    }
    if (best) out.push(best);
  }
  return out;
}

// vecino anterior y siguiente de `me` en ese eje
function sideNeighbors(me, list, ax) {
  const { lo, hi, c } = AX[ax];
  let before = null, after = null;
  for (const q of list) {
    if (!overlaps(me, q, ax)) continue;
    if (q[hi] <= me[c] && (!before || q[hi] > before.q[hi])) before = { q, g: me[lo] - q[hi] };
    if (q[lo] >= me[c] && (!after || q[lo] < after.q[lo])) after = { q, g: q[lo] - me[hi] };
  }
  return { before: before && before.g > 0 ? before : null, after: after && after.g > 0 ? after : null };
}

// posiciones (borde lo) en las que `me` quedaria con separaciones iguales
function spacingTargets(me, list, ax, size) {
  const { lo, hi } = AX[ax];
  const { before, after } = sideNeighbors(me, list, ax);
  const out = [];
  if (before && after && after.q[lo] - before.q[hi] > size) out.push((before.q[hi] + after.q[lo] - size) / 2);
  for (const { g } of neighborGaps(list, ax)) {
    if (before) out.push(before.q[hi] + g);
    if (after) out.push(after.q[lo] - g - size);
  }
  return out;
}

// distancia mas corta (con signo) de alguno de `features` a algun candidato
function nearest(features, cands) {
  let best = null;
  for (const f of features) for (const c of cands) {
    const d = c - f;
    if (Math.abs(d) <= GUIDE_T && (best === null || Math.abs(d) < Math.abs(best))) best = d;
  }
  return best;
}

function computeDrag(panel, o, dir, dx, dy, free) {
  const W = window.innerWidth, H = window.innerHeight;
  const [minW, minH] = MIN_SIZE[panel.kind];
  const others = state.panels.filter((p) => p !== panel).map(edgesOf);
  const grid = (v) => (free ? Math.round(v) : Math.round(v / SNAP) * SNAP);
  const candX = (centers) => others.flatMap((e) => (centers ? [e.l, e.cx, e.r] : [e.l, e.r])).concat(centers ? [W / 2] : []);
  const candY = (centers) => others.flatMap((e) => (centers ? [e.t, e.cy, e.b] : [e.t, e.b])).concat(centers ? [H / 2] : []);
  let { x, y, w, h } = o;
  let snappedW = false, snappedH = false;

  if (dir === "move") {
    x = o.x + dx; y = o.y + dy;
    // la mas cercana entre alinear bordes/centros e igualar separaciones
    const pick = (a, b) => (a === null ? b : b === null ? a : Math.abs(b) < Math.abs(a) ? b : a);
    const me = edgesOf({ x, y, w, h });
    const sx = free ? null : pick(nearest([x, x + w / 2, x + w], candX(true)), nearest([x], spacingTargets(me, others, "x", w)));
    const sy = free ? null : pick(nearest([y, y + h / 2, y + h], candY(true)), nearest([y], spacingTargets(me, others, "y", h)));
    x = sx !== null ? Math.round(x + sx) : grid(x);
    y = sy !== null ? Math.round(y + sy) : grid(y);
    x = clamp(x, -o.w + 80, W - 80);
    y = clamp(y, 0, H - 60);
  } else {
    // borde que se estira -> se imanta a bordes ajenos
    const edge = (pos, cands) => {
      const d = free ? null : nearest([pos], cands);
      return d !== null ? { v: Math.round(pos + d), hit: true } : { v: pos, hit: false };
    };
    if (dir.includes("e")) {
      const r = edge(o.x + o.w + dx, candX(false));
      w = r.hit ? r.v - o.x : grid(o.w + dx);
      snappedW = r.hit;
    }
    if (dir.includes("w")) {
      const l = edge(o.x + dx, candX(false));
      w = l.hit ? o.x + o.w - l.v : grid(o.w - dx);
      snappedW = l.hit;
    }
    if (dir.includes("s")) {
      const b = edge(o.y + o.h + dy, candY(false));
      h = b.hit ? b.v - o.y : grid(o.h + dy);
      snappedH = b.hit;
    }
    if (dir.includes("n")) {
      const t = edge(o.y + dy, candY(false));
      h = t.hit ? o.y + o.h - t.v : grid(o.h - dy);
      snappedH = t.hit;
    }
    // sin borde cerca: misma medida que otro panel
    const sizes = (k) => state.panels.filter((p) => p !== panel).map((p) => p[k]);
    if (!free && /[ew]/.test(dir) && !snappedW) { const d = nearest([w], sizes("w")); if (d !== null) w += d; }
    if (!free && /[ns]/.test(dir) && !snappedH) { const d = nearest([h], sizes("h")); if (d !== null) h += d; }
    w = Math.max(minW, w);
    h = Math.max(minH, h);
    if (isRound(panel)) {
      const side = Math.max(roundMin(panel), /[ew]/.test(dir) && /[ns]/.test(dir) ? Math.max(w, h) : /[ew]/.test(dir) ? w : h);
      w = h = Math.min(side, H, W);
    }
    if (dir.includes("w")) x = o.x + o.w - w;
    if (dir.includes("n")) y = o.y + o.h - h;
  }
  return { x, y, w, h };
}

// Dibuja las guias que se cumplen ahora mismo para `panel`.
function drawGuides(panel, dir) {
  const layer = $("#guides");
  layer.innerHTML = "";
  const W = window.innerWidth, H = window.innerHeight;
  const me = edgesOf(panel);
  const others = state.panels.filter((p) => p !== panel);
  const near = (a, b) => Math.abs(a - b) < 1;
  const moving = dir === "move";
  // en un resize solo cuentan los bordes que se mueven
  const fx = moving ? ["l", "cx", "r"] : [dir.includes("w") && "l", dir.includes("e") && "r"].filter(Boolean);
  const fy = moving ? ["t", "cy", "b"] : [dir.includes("n") && "t", dir.includes("s") && "b"].filter(Boolean);
  const line = (cls, css) => layer.appendChild(Object.assign(el("div", `guide ${cls}`), { style: css }));
  const lines = new Map(); // dedup: "v:123" -> extent
  const addLine = (key, v, a, b) => {
    const cur = lines.get(key);
    lines.set(key, cur ? { v, a: Math.min(cur.a, a), b: Math.max(cur.b, b) } : { v, a, b });
  };
  for (const q of others) {
    const e = edgesOf(q);
    for (const f of fx) for (const g of ["l", "cx", "r"]) {
      if (!moving && g === "cx") continue;
      if (near(me[f], e[g])) addLine(`v:${Math.round(e[g])}`, e[g], Math.min(me.t, e.t) - 12, Math.max(me.b, e.b) + 12);
    }
    for (const f of fy) for (const g of ["t", "cy", "b"]) {
      if (!moving && g === "cy") continue;
      if (near(me[f], e[g])) addLine(`h:${Math.round(e[g])}`, e[g], Math.min(me.l, e.l) - 12, Math.max(me.r, e.r) + 12);
    }
  }
  for (const [key, ln] of lines) {
    if (key[0] === "v") line("v", `left:${ln.v}px; top:${ln.a}px; height:${ln.b - ln.a}px`);
    else line("h", `top:${ln.v}px; left:${ln.a}px; width:${ln.b - ln.a}px`);
  }
  if (moving) drawSpacing(me, others.map(edgesOf), line);
  if (moving && near(me.cx, W / 2)) line("v center", `left:${W / 2}px; top:0; height:${H}px`);
  if (moving && near(me.cy, H / 2)) line("h center", `top:${H / 2}px; left:0; width:${W}px`);

  // misma medida: cotas sobre (ancho) o al lado (alto) de cada panel igual
  const sameW = !moving && /[ew]/.test(dir) ? others.filter((q) => q.w === panel.w) : [];
  const sameH = !moving && /[ns]/.test(dir) ? others.filter((q) => q.h === panel.h) : [];
  if (sameW.length) for (const q of [panel, ...sameW]) line("dim w", `left:${q.x}px; top:${q.y - 14}px; width:${q.w}px`).dataset.label = `↔ ${q.w}`;
  if (sameH.length) for (const q of [panel, ...sameH]) line("dim h", `left:${q.x + q.w + 8}px; top:${q.y}px; height:${q.h}px`).dataset.label = `↕ ${q.h}`;
  return { sameW, sameH };
}

// Marca los huecos iguales: el de `me` con sus vecinos y los de otros
// pares de paneles que miden lo mismo.
function drawSpacing(me, list, line) {
  const same = (a, b) => Math.abs(a - b) <= 1;
  const marks = new Map();
  const mark = (a, b, ax) => {
    const { lo, hi, olo, ohi } = AX[ax];
    const key = `${ax}:${a[lo]},${a[olo]}>${b[lo]},${b[olo]}`;
    const g = b[lo] - a[hi];
    const mid = (Math.max(a[olo], b[olo]) + Math.min(a[ohi], b[ohi])) / 2;
    marks.set(key, ax === "x"
      ? { cls: "gap x", css: `left:${a[hi]}px; top:${mid - 4}px; width:${g}px`, g }
      : { cls: "gap y", css: `top:${a[hi]}px; left:${mid - 4}px; height:${g}px`, g });
  };
  for (const ax of ["x", "y"]) {
    const { before, after } = sideNeighbors(me, list, ax);
    if (before && after && same(before.g, after.g)) { mark(before.q, me, ax); mark(me, after.q, ax); }
    for (const pair of neighborGaps(list, ax)) {
      if (before && same(pair.g, before.g)) { mark(before.q, me, ax); mark(pair.a, pair.b, ax); }
      if (after && same(pair.g, after.g)) { mark(me, after.q, ax); mark(pair.a, pair.b, ax); }
    }
  }
  for (const m of marks.values()) line(m.cls, m.css).dataset.label = String(Math.round(m.g));
}

function bindPanelEditing(panel, rt) {
  rt.el.addEventListener("click", (ev) => {
    if (panel.min && !state.editMode && !ev.target.closest(".ptool")) setMin(panel, false);
  });
  rt.el.addEventListener("dblclick", () => { if (panel.min && state.editMode) setMin(panel, false); });
  rt.el.addEventListener("pointerdown", (ev) => {
    if (!state.editMode || ev.button !== 0) return;
    if (ev.target.closest(".ptool, textarea, [contenteditable='true']")) return;
    ev.preventDefault();
    bringToFront(panel);
    const dir = ev.target.dataset.dir || "move";
    const sx = ev.clientX, sy = ev.clientY;
    const o = { x: panel.x, y: panel.y, w: panel.w, h: panel.h };
    const badge = $(".size-badge", rt.el);
    rt.el.classList.add("dragging");

    const onMove = (mv) => {
      // Ctrl = mover libre (sin guias ni rejilla)
      Object.assign(panel, computeDrag(panel, o, dir, mv.clientX - sx, mv.clientY - sy, mv.ctrlKey));
      applyGeometry(panel);
      const { sameW, sameH } = drawGuides(panel, dir);
      if (dir === "move") {
        badge.textContent = `${panel.x}, ${panel.y}`;
      } else {
        const tag = (list) => (list.length ? ` = ${list[0].title.toUpperCase()}${list.length > 1 ? ` +${list.length - 1}` : ""}` : "");
        badge.textContent = `${panel.w}${tag(sameW)} × ${panel.h}${tag(sameH)}`;
      }
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      rt.el.classList.remove("dragging");
      $("#guides").innerHTML = "";
      saveBoard();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
}

function addPanel(panel) {
  state.panels.push(panel);
  createPanel(panel);
  refreshCodes();
  saveBoard();
  return panel;
}

function removePanel(id) {
  const rt = runtime.get(id);
  if (rt) {
    if (rt.content && rt.content.destroy) rt.content.destroy();
    rt.el.remove();
    runtime.delete(id);
  }
  state.panels = state.panels.filter((p) => p.id !== id);
  refreshCodes();
  syncTerm();
  syncAddTerminalButton();
  saveBoard();
}

// Siguiente lugar libre aproximado: centro de la pantalla, escalonado.
let spawnOffset = 0;
function spawnRect(w, h) {
  spawnOffset = (spawnOffset + 1) % 6;
  const x = Math.round((window.innerWidth - w) / 2 + spawnOffset * 24);
  const y = Math.round((window.innerHeight - h) / 2 + spawnOffset * 24);
  return { x, y, w, h };
}

function addAppsPanel() {
  const n = state.panels.filter((p) => p.kind === "apps").length + 1;
  const p = addPanel({ id: newId(), kind: "apps", title: `Apps ${n}`, apps: [], autoroll: false, ...spawnRect(380, 460) });
  openPicker(p.id);
}

function addTerminalPanel() {
  if (terminalPanel()) { toast("YA HAY UNA TERMINAL"); return; }
  addPanel({ id: newId(), kind: "terminal", title: "Terminal", collapsed: false, ...defaultTerminalRect() });
  syncAddTerminalButton();
}

function addInfoPanel(widget) {
  const def = WIDGETS[widget];
  addPanel({ id: newId(), kind: "info", widget, title: def.name, ...spawnRect(ROUND_NEW, ROUND_NEW) });
}

// Cuadra un panel de info para hacerlo redondo (centrado en su rectángulo)
function squareUp(p) {
  p.rect = { x: p.x, y: p.y, w: p.w, h: p.h }; // para volver a rectangular tal cual estaba
  const side = Math.max(roundMin(p), Math.min(p.w, p.h));
  p.x = Math.round(p.x + (p.w - side) / 2);
  p.y = Math.round(p.y + (p.h - side) / 2);
  p.w = p.h = side;
}

function toggleShape(panel) {
  const rt = runtime.get(panel.id);
  if (isRound(panel)) {
    panel.shape = "rect";
    panel.min = false;
    if (panel.rect) Object.assign(panel, panel.rect);
    else {
      const [w, h] = panel.kind === "apps" ? [480, 500] : (WIDGETS[panel.widget] || WIDGETS.clock).size;
      panel.x = Math.round(panel.x + (panel.w - w) / 2); panel.y = Math.round(panel.y + (panel.h - h) / 2);
      panel.w = w; panel.h = h;
    }
  } else {
    panel.shape = "round";
    squareUp(panel);
  }
  buildTools(panel, rt);
  buildBody(panel, rt);
  applyGeometry(panel);
  syncShapeAll();
  saveBoard();
}

// Botón de la barra de edición: todos los paneles de info a la vez
function syncShapeAll() {
  const b = $("#shape-all");
  if (!b) return;
  const info = state.panels.filter((p) => p.kind === "info" || p.kind === "apps");
  const allRound = info.length > 0 && info.every(isRound);
  b.textContent = allRound ? "▭ RECTANGULARES" : "◯ REDONDOS";
}
$("#shape-all").addEventListener("click", () => {
  const info = state.panels.filter((p) => p.kind === "info" || p.kind === "apps");
  const toRound = !(info.length > 0 && info.every(isRound));
  for (const p of info) if (isRound(p) !== toRound) toggleShape(p);
  toast(toRound ? "◯ PANELES REDONDOS" : "▭ PANELES RECTANGULARES");
  syncShapeAll();
});

function setMin(panel, on) {
  panel.min = on;
  applyGeometry(panel);
  saveBoard();
}

function changeInfoWidget(panelId, widget) {
  const panel = state.panels.find((p) => p.id === panelId);
  if (!panel) return;
  const oldDef = WIDGETS[panel.widget];
  if (!oldDef || panel.title === oldDef.name) panel.title = WIDGETS[widget].name;
  panel.widget = widget;
  const rt = runtime.get(panelId);
  rt.title.textContent = panel.title;
  buildBody(panel, rt);
  saveBoard();
}

function removeAppFromPanel(panelId, appId) {
  const panel = state.panels.find((p) => p.id === panelId);
  if (!panel) return;
  if (panel.autoAll) {
    if (!Array.isArray(panel.hidden)) panel.hidden = [];
    if (!panel.hidden.includes(appId)) panel.hidden.push(appId);
  } else {
    panel.apps = panel.apps.filter((id) => id !== appId);
  }
  refreshPanelApps(panel);
  saveBoard();
  toast(panel.autoAll ? "✕ APP OCULTA EN ESTE PANEL (+ PARA VOLVER A MOSTRARLA)" : "✕ APP QUITADA");
}

function refreshPanelApps(panel) {
  const rt = runtime.get(panel.id);
  if (!rt || !isAppsView(rt.content)) return;
  const r = rt.content;
  r.setApps(panelApps(panel));
  r.lastCenter = -1;
  if (r.items.length) {
    r.pos = r.target = mod(Math.round(r.pos), r.items.length);
    r.mode = "idle";
  }
  r.render();
}

// ============================================================
// Layout por defecto (se adapta al tamaño de la pantalla)
// ============================================================
function defaultPanels(seedApps) {
  const W = window.innerWidth, H = window.innerHeight;
  const top = 64, bottom = 40, m = 28, gap = 22;
  const colW = clamp(Math.round(W * 0.21), 300, 420);
  const avail = H - top - bottom;

  const stack = (x, specs) => {
    const totalW = specs.reduce((s, sp) => s + sp.weight, 0);
    const free = avail - gap * (specs.length - 1);
    let y = top;
    return specs.map((sp) => {
      const h = Math.round((free * sp.weight) / totalW);
      const p = { id: newId(), kind: "info", widget: sp.widget, title: WIDGETS[sp.widget].name, x, y, w: colW, h };
      y += h + gap;
      return p;
    });
  };

  const left = stack(m, [
    { widget: "clock", weight: 0.95 },
    { widget: "system", weight: 1.25 },
    { widget: "network", weight: 1.05 },
    { widget: "battery", weight: 0.75 },
  ]);
  const right = stack(W - m - colW, [
    { widget: "cpu", weight: 1.05 },
    { widget: "cores", weight: 0.95 },
    { widget: "processes", weight: 1.2 },
    { widget: "host", weight: 0.85 },
  ]);
  const term = { id: newId(), kind: "terminal", title: "Terminal", collapsed: false, ...defaultTerminalRect() };
  const aw = clamp(Math.round(W * 0.25), 320, 500);
  const ah = Math.round(Math.min(term.y - top - gap, 760));
  const apps = {
    id: newId(), kind: "apps", title: "Apps", apps: seedApps.slice(), autoAll: true, hidden: [], autoroll: false,
    x: Math.round((W - aw) / 2), y: top, w: aw, h: ah,
  };
  return [...left, apps, ...right, term];
}

// Terminal: abajo al centro, encima de la cinta inferior.
function defaultTerminalRect() {
  const W = window.innerWidth, H = window.innerHeight;
  const w = Math.round(clamp(W * 0.42, 560, 900));
  const h = Math.round(clamp(H * 0.34, 220, 380));
  return { x: Math.round((W - w) / 2), y: H - 24 - 14 - h, w, h };
}

function resetBoard() {
  const appIds = [];
  for (const p of state.panels) for (const id of p.apps || []) if (!appIds.includes(id)) appIds.push(id);
  for (const p of state.panels.slice()) {
    const rt = runtime.get(p.id);
    if (rt && rt.content && rt.content.destroy) rt.content.destroy();
    if (rt) rt.el.remove();
    runtime.delete(p.id);
  }
  state.panels = defaultPanels(appIds);
  state.panels.forEach(createPanel);
  refreshCodes();
  syncAddTerminalButton();
  syncShapeAll();
  saveBoard();
  toast("◢ DISEÑO RESTABLECIDO");
}

// ============================================================
// Selector de apps
// ============================================================
const picker = $("#picker");
const pickerSearch = $("#picker-search");
const pickerList = $("#picker-list");
let pickerPanelId = null;

function openPicker(panelId) {
  const panel = state.panels.find((p) => p.id === panelId);
  if (!panel) return;
  pickerPanelId = panelId;
  $("#picker-title").textContent = `AGREGAR A: ${panel.title.toUpperCase()}`;
  pickerSearch.value = "";
  picker.hidden = false;
  if (allApps === null) post("requestAllApps", "");
  renderPicker();
  setTimeout(() => pickerSearch.focus(), 30);
}

function closePicker() {
  picker.hidden = true;
  pickerPanelId = null;
}

function renderPicker() {
  pickerList.innerHTML = "";
  if (allApps === null) {
    pickerList.innerHTML = '<div class="picker-empty">CARGANDO APPS...</div>';
    return;
  }
  const panel = state.panels.find((p) => p.id === pickerPanelId);
  if (!panel) return;
  const q = pickerSearch.value.trim().toLowerCase();
  const list = allApps.filter((a) => a.name.toLowerCase().includes(q));
  if (!list.length) {
    pickerList.innerHTML = '<div class="picker-empty">SIN RESULTADOS</div>';
    return;
  }
  for (const app of list) {
    const added = inPanel(panel, app.id);
    const row = el("div", `picker-row${added ? " added" : ""}`);
    if (app.icon) {
      const img = el("img");
      img.src = app.icon;
      row.appendChild(img);
    }
    row.appendChild(el("span", "pr-name")).textContent = app.name;
    row.appendChild(el("span", "pr-state")).textContent = added ? "✓ EN EL PANEL" : "+ AGREGAR";
    row.addEventListener("click", () => {
      state.apps[app.id] = app;
      toggleInPanel(panel, app.id);
      refreshPanelApps(panel);
      saveBoard();
      renderPicker();
    });
    pickerList.appendChild(row);
  }
}

window.__setAllApps = function (apps) {
  // apps nuevas desde la ultima lista (no en la primera: seria todo)
  const nuevas = allApps === null ? [] : apps.filter((a) => !allApps.some((b) => b.id === a.id));
  allApps = apps;
  for (const a of apps) state.apps[a.id] = a; // nombre / icono pueden haber cambiado
  for (const p of state.panels) if (p.kind === "apps" && p.autoAll) refreshPanelApps(p);
  if (nuevas.length) toast(`◆ NUEVA APP: ${nuevas.map((a) => a.name.toUpperCase()).join(" · ")}`);
  if (!picker.hidden) renderPicker();
};

pickerSearch.addEventListener("input", renderPicker);
$("#picker-close").addEventListener("click", closePicker);
picker.addEventListener("pointerdown", (ev) => { if (ev.target === picker) closePicker(); });

// ============================================================
// Menu de tipos de info
// ============================================================
const infoMenu = $("#info-menu");
let infoMenuTarget = null; // null = nuevo panel, si no id del panel a cambiar

function openInfoMenu(panelId = null) {
  infoMenuTarget = panelId;
  const current = panelId ? state.panels.find((p) => p.id === panelId).widget : null;
  $("#info-menu-title").textContent = panelId ? "CAMBIAR TIPO DE PANEL" : "NUEVO PANEL DE INFO";
  const list = $("#info-menu-list");
  list.innerHTML = "";
  for (const [key, def] of Object.entries(WIDGETS)) {
    const opt = el("button", `info-opt${key === current ? " current" : ""}`, `<span class="io-name">${def.name}</span><span class="io-desc">${def.desc}</span>`);
    opt.addEventListener("click", () => {
      infoMenu.hidden = true;
      if (infoMenuTarget) changeInfoWidget(infoMenuTarget, key);
      else addInfoPanel(key);
    });
    list.appendChild(opt);
  }
  infoMenu.hidden = false;
}
$("#info-menu-close").addEventListener("click", () => (infoMenu.hidden = true));
infoMenu.addEventListener("pointerdown", (ev) => { if (ev.target === infoMenu) infoMenu.hidden = true; });

// ============================================================
// Selector de temas: cuales salen en la barra superior
// ============================================================
const themeMenu = $("#theme-menu");

function openThemeMenu() {
  renderThemeMenu();
  themeMenu.hidden = false;
}

function renderThemeMenu() {
  const list = $("#theme-list");
  list.innerHTML = "";
  for (const t of THEMES) {
    const inBar = state.themeBar.includes(t.id);
    const row = el("div", `theme-row${inBar ? " in-bar" : ""}${t.id === state.theme ? " current" : ""}`);
    const sw = el("div", "tr-swatch");
    for (const k of ["bg2", "accent", "alt", "warn", "text"]) {
      const i = el("i");
      i.style.background = t.colors[k] || t.colors.bg;
      sw.appendChild(i);
    }
    const txt = el("div", "tr-text");
    txt.append(el("span", "tr-name"), el("span", "tr-desc"));
    txt.children[0].textContent = t.name;
    txt.children[1].textContent = `${t.desc} · ${{ synth: "fondo synthwave", plain: "fondo liso" }[t.style] || "fondo rejilla"}`;
    const st = el("span", "tr-state", inBar ? "✓ EN LA BARRA" : "+ AGREGAR");
    const tryBtn = el("button", "hud-btn tr-try", "VER");
    tryBtn.title = "Aplicar este tema ahora";
    tryBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      applyTheme(t.id, { flash: true, persist: true });
      renderThemeMenu();
    });
    row.append(sw, txt, st, tryBtn);
    row.addEventListener("click", () => toggleThemeInBar(t.id));
    list.appendChild(row);
  }
}

function toggleThemeInBar(id) {
  const bar = state.themeBar;
  if (bar.includes(id)) {
    if (bar.length === 1) { toast("DEBE QUEDAR AL MENOS UN TEMA"); return; }
    state.themeBar = bar.filter((x) => x !== id);
    // si se quito el tema activo, pasar al primero que queda en la barra
    if (state.theme === id) applyTheme(state.themeBar[0], { flash: true });
  } else {
    if (bar.length >= MAX_THEME_BAR) { toast(`MÁXIMO ${MAX_THEME_BAR} TEMAS EN LA BARRA`); return; }
    state.themeBar = [...bar, id];
  }
  renderThemeBar();
  renderThemeMenu();
  saveBoard();
}

$("#open-themes").addEventListener("click", openThemeMenu);
$("#theme-menu-close").addEventListener("click", () => (themeMenu.hidden = true));
themeMenu.addEventListener("pointerdown", (ev) => { if (ev.target === themeMenu) themeMenu.hidden = true; });

// ============================================================
// Modo edicion
// ============================================================
function setEditMode(on) {
  state.editMode = on;
  document.body.classList.toggle("edit-mode", on);
  $("#edit-toggle").textContent = on ? "✓ LISTO" : "⚙ EDITAR";
  runtime.forEach((rt) => isAppsView(rt.content) && rt.content.syncAuto());
  const tp = terminalPanel();
  if (tp) applyGeometry(tp); // en edicion se ve desplegada (y la VTE se oculta)
  if (!on) saveBoard();
}

$("#edit-toggle").addEventListener("click", () => setEditMode(!state.editMode));
$("#edit-done").addEventListener("click", () => setEditMode(false));
$("#add-apps-panel").addEventListener("click", addAppsPanel);
$("#add-info-panel").addEventListener("click", () => openInfoMenu(null));
$("#add-terminal-panel").addEventListener("click", addTerminalPanel);

function armButton(btn, label, action) {
  if (btn.classList.contains("armed")) { action(); btn.classList.remove("armed"); btn.textContent = btn.dataset.label; return; }
  btn.dataset.label = btn.dataset.label || btn.textContent;
  btn.classList.add("armed");
  btn.textContent = label;
  setTimeout(() => { btn.classList.remove("armed"); btn.textContent = btn.dataset.label; }, 3000);
}
$("#reset-board").addEventListener("click", (ev) => armButton(ev.currentTarget, "¿SEGURO? CLIC OTRA VEZ", resetBoard));
$("#quit-wallpaper").addEventListener("click", (ev) => armButton(ev.currentTarget, "¿CERRAR? CLIC OTRA VEZ", () => { flushSave(); post("quitApp", ""); }));

document.addEventListener("keydown", (ev) => {
  const t = ev.target;
  if (t.closest && t.closest("input, textarea, [contenteditable='true']")) {
    if (ev.key === "Escape") { if (!picker.hidden) closePicker(); t.blur(); }
    return;
  }
  if (ev.key === "Escape") {
    if (!picker.hidden) closePicker();
    else if (!infoMenu.hidden) infoMenu.hidden = true;
    else if (!themeMenu.hidden) themeMenu.hidden = true;
    else if (state.editMode) setEditMode(false);
  } else if (ev.key.toLowerCase() === "e" && !ev.ctrlKey && !ev.altKey) {
    setEditMode(!state.editMode);
  } else if (ev.key.toLowerCase() === "t" && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
    toggleTerminal();
  }
});

// ============================================================
// Terminal (la VTE real vive en Python, encima de la pagina)
// ============================================================
const TERM_FOLDED_H = 34; // alto de la barra cuando esta plegada
const TERM_ANIM_MS = 200;
let termAnimating = false;
let lastTermRect = "";

const terminalPanel = () => state.panels.find((p) => p.kind === "terminal");

function buildTerminalBody(root) {
  // Lo que se ve si la VTE esta oculta (modo edicion o con un menu abierto).
  const w = el("div", "w w-term", '<div class="term-ghost"><span class="tg-prompt">[bash ~]$</span> <span class="tg-cursor">█</span></div><div class="term-ghost-note">TERMINAL EN PAUSA · SAL DEL MODO EDICIÓN PARA USARLA</div>');
  root.appendChild(w);
  return {};
}

function bindTerminalPanel(panel, rt) {
  // la VTE aparece cuando termina la animacion de entrada del panel
  rt.el.classList.add("entering");
  setTimeout(() => { rt.el.classList.remove("entering"); syncTerm(); }, 650);
  // Plegada: clic en la barra la despliega. Desplegada: clic en la barra
  // le da el foco a la terminal.
  $(".panel-head", rt.el).addEventListener("click", (ev) => {
    if (state.editMode || ev.target.closest(".ptool")) return;
    if (panel.collapsed) toggleTerminal();
    else post("termFocus", true);
  });
}

function syncFoldButton(panel, rt) {
  if (!rt.foldBtn) return;
  rt.foldBtn.textContent = panel.collapsed ? "▲" : "▼";
  rt.foldBtn.title = panel.collapsed ? "Desplegar la terminal (T o F12)" : "Plegar la terminal (F12)";
}

function syncAddTerminalButton() {
  $("#add-terminal-panel").hidden = !!terminalPanel();
}

function toggleTerminal(force) {
  const panel = terminalPanel();
  if (!panel) { toast("SIN TERMINAL · AGRÉGALA EN MODO EDICIÓN"); return; }
  if (state.editMode) return;
  const collapsed = force === undefined ? !panel.collapsed : !force;
  if (collapsed === !!panel.collapsed) return;
  panel.collapsed = collapsed;
  const rt = runtime.get(panel.id);
  syncFoldButton(panel, rt);
  // la VTE se esconde mientras el marco se pliega/despliega y vuelve al final
  termAnimating = true;
  syncTerm();
  rt.el.classList.add("term-anim");
  applyGeometry(panel);
  setTimeout(() => {
    rt.el.classList.remove("term-anim");
    termAnimating = false;
    syncTerm(!collapsed); // al desplegar, el teclado va directo a la terminal
  }, TERM_ANIM_MS + 20);
  saveBoard();
}
window.__termToggle = () => toggleTerminal();

// Le dice a Python donde poner la terminal (o que la oculte).
function syncTerm(focus = false) {
  const panel = terminalPanel();
  const rt = panel && runtime.get(panel.id);
  const visible = !!rt && !panel.collapsed && !state.editMode && !termAnimating &&
    picker.hidden && infoMenu.hidden && themeMenu.hidden && !rt.el.classList.contains("entering");
  document.body.classList.toggle("term-open", visible);
  let msg = { visible: false };
  if (visible) {
    const r = rt.body.getBoundingClientRect();
    msg = { visible: true, x: r.left + 10, y: r.top + 5, w: r.width - 10 - 22, h: r.height - 5 - 3 };
  }
  const key = JSON.stringify(msg);
  if (key === lastTermRect && !focus) return;
  lastTermRect = key;
  post("termRect", JSON.stringify({ ...msg, focus }));
}

// Paleta de la terminal a partir del tema activo. Un tema puede fijar
// cualquier color con variables CSS (--term-fg, --term-bg, --term-cursor,
// --term-selection, --term-ansi-0 ... --term-ansi-15); lo que no fije se
// calcula de sus colores (--accent, --alt, --warn, --text, --muted, --bg).
// Asi un tema nuevo tiene terminal a juego sin tocar nada mas.
const colorProbe = document.createElement("span");
function parseColor(css) {
  if (!colorProbe.isConnected) { colorProbe.style.display = "none"; document.body.appendChild(colorProbe); }
  colorProbe.style.color = "";
  colorProbe.style.color = css;
  const m = getComputedStyle(colorProbe).color.match(/[\d.]+/g) || [0, 0, 0];
  return { r: +m[0], g: +m[1], b: +m[2], a: m[3] == null ? 1 : +m[3] };
}
function rgbToHsl({ r, g, b }) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: h * 60, s, l };
}
function hslToRgb({ h, s, l }) {
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))));
  return { r: f(0), g: f(8), b: f(4), a: 1 };
}
const toHex = ({ r, g, b }) => "#" + [r, g, b].map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, "0")).join("");
const mixRgb = (a, b, t) => ({ r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t, a: 1 });
function luminance({ r, g, b }) {
  const ch = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}
const contrast = (a, b) => { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
// aclara un color hasta que se lea bien sobre el fondo
function readable(c, bg, min = 4.5) {
  let hsl = rgbToHsl(c), out = c;
  for (let i = 0; i < 20 && contrast(out, bg) < min && hsl.l < 0.95; i++) {
    hsl = { ...hsl, l: Math.min(0.95, hsl.l + 0.03) };
    out = hslToRgb(hsl);
  }
  return out;
}
const hueDist = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };

function termPalette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  const bg = parseColor(v("--bg")), text = parseColor(v("--text")), muted = parseColor(v("--muted"));
  const accent = parseColor(v("--accent")), alt = parseColor(v("--alt")), warn = parseColor(v("--warn"));
  // fondo real sobre el que se lee: el del panel mezclado con el de la pagina
  const pbg = parseColor(v("--panel-bg"));
  const ground = mixRgb(bg, pbg, pbg.a);

  // Los 6 colores ANSI (rojo, verde, amarillo, azul, magenta, cian). Los del
  // tema (acento, alterno, aviso) ocupan el hueco de tono mas parecido; el
  // resto se genera con la misma intensidad, levemente teñido al acento.
  const slots = [
    { name: "red", hue: 0 }, { name: "green", hue: 125 }, { name: "yellow", hue: 52 },
    { name: "blue", hue: 222 }, { name: "magenta", hue: 300 }, { name: "cyan", hue: 186 },
  ];
  // Se busca el reparto de menor distancia total; un color del tema que no
  // se parece a ningun hueco libre (o es gris, como el blanco de MONO) no
  // ocupa ninguno y ese hueco se genera.
  const anchors = [accent, alt, warn].map((c) => ({ c, hsl: rgbToHsl(c) })).filter((a) => a.hsl.s > 0.25);
  const MISS = 46; // costo de dejar un color del tema sin hueco
  let best = { cost: Infinity, pick: [] };
  const search = (i, used, cost, pick) => {
    if (cost >= best.cost) return;
    if (i === anchors.length) { best = { cost, pick: pick.slice() }; return; }
    search(i + 1, used, cost + MISS, [...pick, -1]);
    slots.forEach((sl, j) => {
      const d = hueDist(anchors[i].hsl.h, sl.hue);
      if (!used.has(j) && d < MISS) search(i + 1, new Set([...used, j]), cost + d, [...pick, j]);
    });
  };
  search(0, new Set(), 0, []);
  best.pick.forEach((j, i) => { if (j >= 0) slots[j].color = anchors[i].c; });
  const accHsl = rgbToHsl(anchors.length ? anchors[0].c : accent);
  const sat = anchors.length ? clamp(anchors.reduce((t, a) => t + a.hsl.s, 0) / anchors.length, 0.65, 0.95) : 0.8;
  for (const s of slots) {
    if (s.color) continue;
    let h = s.hue;
    const d = ((accHsl.h - h + 540) % 360) - 180; // tiñe un 12% hacia el acento
    h = (h + d * 0.12 + 360) % 360;
    s.color = hslToRgb({ h, s: sat, l: 0.6 });
  }
  const bright = (c) => { const h = rgbToHsl(c); return hslToRgb({ h: h.h, s: Math.min(1, h.s * 0.95), l: Math.min(0.84, h.l + 0.13) }); };

  const by = Object.fromEntries(slots.map((s) => [s.name, readable(s.color, ground)]));
  const order = ["red", "green", "yellow", "blue", "magenta", "cyan"];
  const black = mixRgb(ground, muted, 0.35);
  const white = mixRgb(text, muted, 0.25);
  const ansi = [
    black, ...order.map((n) => by[n]), white,
    readable(muted, ground, 3.2), ...order.map((n) => bright(by[n])), mixRgb(text, { r: 255, g: 255, b: 255 }, 0.5),
  ].map(toHex);
  for (let i = 0; i < 16; i++) { const o = v(`--term-ansi-${i}`); if (o) ansi[i] = toHex(parseColor(o)); }

  const pick = (name, fallback) => (v(name) ? toHex(parseColor(v(name))) : fallback);
  const fg = pick("--term-fg", toHex(text));
  return {
    fg,
    bg: v("--term-bg") || "rgba(0, 0, 0, 0)", // transparente: se ve el panel detras
    cursor: pick("--term-cursor", toHex(alt)),
    cursorText: toHex(ground),
    selection: pick("--term-selection", toHex(mixRgb(ground, accent, 0.4))),
    selectionText: fg,
    ansi,
  };
}

function postTermTheme() {
  post("termTheme", JSON.stringify(termPalette()));
}

// Menus abiertos tapan la terminal en la pagina: ocultar la VTE mientras.
new MutationObserver(() => syncTerm()).observe(picker, { attributes: true, attributeFilter: ["hidden"] });
new MutationObserver(() => syncTerm()).observe(infoMenu, { attributes: true, attributeFilter: ["hidden"] });
new MutationObserver(() => syncTerm()).observe(themeMenu, { attributes: true, attributeFilter: ["hidden"] });

// ============================================================
// Metricas / reloj / cinta
// ============================================================
window.__updateMetrics = function (m) {
  state.metrics = m;
  pushHist(hist.cpu, m.cpu_percent);
  pushHist(hist.up, m.net_up_bps);
  pushHist(hist.down, m.net_down_bps);
  setSaver(m.battery_percent != null && !m.battery_charging);
  if (power.paused) return;
  runtime.forEach((rt) => rt.content && rt.content.update && rt.content.update(m));
  $("#hud-host").textContent = `${(m.user || "").toUpperCase()}@${(m.hostname || "--").toUpperCase()}`;
  updateTicker(m);
};

let tickerStamp = 0;
function updateTicker(m) {
  if (Date.now() - tickerStamp < 60000) return; // reescribir la cinta reinicia su animacion
  tickerStamp = Date.now();
  const parts = [
    `ENLACE NEURAL <em>ESTABLE</em>`,
    `KERNEL <em>${m.kernel || "--"}</em>`,
    `ENCENDIDO <em>${fmtDuration(m.uptime_s)}</em>`,
    `CPU <em>${m.cpu_percent ?? "--"}%</em>`,
    `RAM <em>${m.ram_used_gb ?? "--"}/${m.ram_total_gb ?? "--"} GB</em>`,
    `DISCO <em>${m.disk_used_gb ?? "--"}/${m.disk_total_gb ?? "--"} GB</em>`,
    `PROTOCOLO DE INTRUSIÓN <em>INACTIVO</em>`,
    `TECLA <em>E</em> = MODO EDICIÓN`,
    `TECLA <em>T</em> O <em>F12</em> = DESPLEGAR / PLEGAR LA TERMINAL`,
    `RUEDA DEL MOUSE SOBRE UN PANEL DE APPS = GIRAR LA RULETA`,
  ];
  $("#ticker-track").innerHTML = parts.join("<b>◆</b>") + "<b>◆</b>";
}

function tickSecond() {
  if (power.paused) return;
  const now = new Date();
  $("#hud-clock").textContent = `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
  runtime.forEach((rt) => rt.content && rt.content.tick && rt.content.tick());
}
setInterval(tickSecond, 1000);

// Glitch ocasional en el titulo de un panel al azar.
setInterval(() => {
  if (!state.panels.length || document.hidden || power.paused || power.saver || power.still) return;
  const p = state.panels[Math.floor(Math.random() * state.panels.length)];
  const rt = runtime.get(p.id);
  if (!rt) return;
  rt.el.classList.add("glitch");
  setTimeout(() => rt.el.classList.remove("glitch"), 500);
}, 7000);

// ============================================================
// Init desde el backend
// ============================================================
window.__init = function ({ board, apps, seedApps }) {
  state.apps = apps || {};
  const bar = (Array.isArray(board.themes) ? board.themes : DEFAULT_THEME_BAR).filter((id) => themeById(id));
  state.themeBar = bar.length ? bar.slice(0, MAX_THEME_BAR) : DEFAULT_THEME_BAR.slice();
  applyTheme(board.theme);
  for (const p of state.panels) { const rt = runtime.get(p.id); if (rt) rt.el.remove(); }
  runtime.clear();
  const fresh = !Array.isArray(board.panels);
  state.panels = fresh ? defaultPanels(seedApps || []) : board.panels;
  // tableros de antes de la terminal: se agrega una, abajo al centro
  const migrate = !fresh && (board.version || 2) < 3 && !state.panels.some((p) => p.kind === "terminal");
  if (migrate) state.panels.push({ id: newId(), kind: "terminal", title: "Terminal", collapsed: false, ...defaultTerminalRect() });
  // v4: el primer panel de apps pasa a automatico (todas las instaladas)
  const toV4 = !fresh && (board.version || 2) < 4;
  if (toV4) {
    const first = state.panels.find((p) => p.kind === "apps");
    if (first) { first.autoAll = true; first.hidden = []; }
  }
  // v5: los paneles de info pasan a redondos (cuadrados, centrados donde estaban)
  const toV5 = !fresh && (board.version || 2) < 5;
  if (toV5 || fresh) for (const p of state.panels) if (p.kind === "info" && !p.shape) squareUp(p);
  // v6: el panel de apps pasa a reactor redondo (▭ lo devuelve a la ruleta)
  const toV6 = !fresh && (board.version || 2) < 6;
  if (toV6 || fresh) for (const p of state.panels) if (p.kind === "apps" && !p.shape) squareUp(p);
  // por si se cambio la resolucion: que ningun panel quede fuera de la pantalla
  const W = window.innerWidth, H = window.innerHeight;
  for (const p of state.panels) {
    const [minW, minH] = MIN_SIZE[p.kind] || MIN_SIZE.info;
    if (isRound(p)) { const sd = clamp(Math.max(p.w, p.h), roundMin(p), Math.min(W, H)); p.w = p.h = sd; }
    else { p.w = clamp(p.w, minW, W); p.h = clamp(p.h, minH, H); }
    p.x = clamp(p.x, -p.w + 80, W - 80); p.y = clamp(p.y, 0, H - 60);
    if (p.kind === "terminal" && p.y + p.h > H) p.y = Math.max(0, H - p.h); // su barra plegada va abajo
    if (p.kind === "apps" && !Array.isArray(p.apps)) p.apps = [];
  }
  state.panels.forEach(createPanel);
  refreshCodes();
  syncAddTerminalButton();
  syncShapeAll();
  postTermTheme();
  tickSecond();
  if (fresh || migrate || toV4 || toV5 || toV6) saveBoard();
  if (state.panels.some((p) => p.kind === "apps" && p.autoAll)) post("requestAllApps", "");
};

// ============================================================
// Fondo y ambiente.
// Todo lo "estatico" (rejilla, sol, piso) se dibuja una sola vez y solo se
// MUEVE con transform (lo compone la GPU, casi gratis). Un unico reloj de
// ambiente a ~12 fps mueve rejilla, piso, barrido, cinta y paquetes: las
// animaciones CSS infinitas obligan a WebKit a generar 60 cuadros/s y en
// esta laptop eso costaba ~70% de un nucleo solo por tener el fondo puesto.
// ============================================================
const GRID = 48; // px entre lineas de la rejilla
const GRID_TILE = GRID * 4;
const AMBIENT_FPS = 12;
const SYNTH_HORIZON = 0.58; // igual que top: 58% en el CSS
const HLINES = 14; // lineas horizontales del piso synthwave

function makeGridTile() {
  const c = document.createElement("canvas");
  c.width = c.height = GRID_TILE;
  const ctx = c.getContext("2d");
  ctx.fillStyle = colors.grid;
  for (let i = 0; i < GRID_TILE; i += GRID) {
    ctx.fillRect(i, 0, 1, GRID_TILE);
    ctx.fillRect(0, i, GRID_TILE, 1);
  }
  ctx.fillStyle = `rgba(${colors.accentRgb}, 0.28)`; // cruz en la esquina del mosaico
  ctx.fillRect(0, 0, 5, 1); ctx.fillRect(GRID_TILE - 4, 0, 4, 1);
  ctx.fillRect(0, 0, 1, 5); ctx.fillRect(0, GRID_TILE - 4, 1, 4);
  $("#bg-grid").style.backgroundImage = `url(${c.toDataURL()})`;
}

// Lineas del piso que convergen al horizonte: estaticas, se dibujan una vez.
function makeFloor() {
  const c = $("#bg-floor-v");
  const w = (c.width = c.clientWidth), h = (c.height = c.clientHeight);
  if (!w || !h) return;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = `rgba(${colors.accentRgb}, 0.45)`;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = -26; i <= 26; i++) {
    ctx.moveTo(w / 2 + i * 7, 0);
    ctx.lineTo(w / 2 + i * (w / 12), h);
  }
  ctx.stroke();
  const fog = ctx.createLinearGradient(0, 0, 0, h * 0.35);
  fog.addColorStop(0, "rgba(0, 0, 0, 0.75)");
  fog.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.globalCompositeOperation = "destination-out";
  ctx.fillStyle = fog;
  ctx.fillRect(0, 0, w, h * 0.35);
  ctx.globalCompositeOperation = "source-over";
}

const hlines = [];
function makeHlines() {
  const box = $("#bg-hlines");
  box.innerHTML = "";
  hlines.length = 0;
  for (let i = 0; i < HLINES; i++) hlines.push(box.appendChild(el("i", "hline")));
}

const packets = [];
function makePackets() {
  const box = $("#bg-packets");
  box.innerHTML = "";
  packets.length = 0;
  const rows = Math.ceil((window.innerHeight + GRID_TILE * 2) / GRID);
  for (let i = 0; i < 12; i++) {
    const node = el("i", "packet");
    const p = { node, x: Math.random(), v: 0.02 + Math.random() * 0.05, len: 30 + Math.random() * 90 };
    node.style.width = `${Math.round(p.len)}px`;
    node.style.top = `${Math.floor(Math.random() * rows) * GRID - 1}px`;
    box.appendChild(node);
    packets.push(p);
  }
}

const ambient = {
  t0: performance.now(),
  grid: $("#bg-grid"),
  sweep: $(".fx-sweep"),
  ticker: $("#ticker-track"),
  pending: false,
};

function ambientFrame(now) {
  ambient.pending = false;
  const s = (now - ambient.t0) / 1000;
  const W = window.innerWidth, H = window.innerHeight;
  if (currentTheme().style === "synth") {
    // Lineas horizontales que vienen hacia uno: z^2 da el efecto de perspectiva.
    const floorH = H * (1 - SYNTH_HORIZON);
    const phase = (s * 0.55) % 1;
    hlines.forEach((node, i) => {
      const z = (i + phase) / HLINES;
      node.style.transform = `translate3d(0, ${(floorH * z * z).toFixed(1)}px, 0) scaleY(${(0.6 + z * 1.6).toFixed(2)})`;
      node.style.opacity = (0.15 + z * 0.85).toFixed(2);
    });
  } else {
    const d = (s * 8) % GRID_TILE;
    ambient.grid.style.transform = `translate3d(${d.toFixed(1)}px, ${d.toFixed(1)}px, 0)`;
    for (const p of packets) {
      p.x += p.v / AMBIENT_FPS;
      if (p.x > 1.15) p.x = -0.15;
      p.node.style.transform = `translate3d(${Math.round(p.x * (W + GRID_TILE * 2))}px, 0, 0)`;
    }
  }
  ambient.sweep.style.transform = `translate3d(0, ${(((s / 9) % 1) * (H + 400) - 200).toFixed(0)}px, 0)`;
  spinFrame(s);
  const tw = ambient.ticker.scrollWidth || 1;
  ambient.ticker.style.transform = `translate3d(${-((s * 45) % tw).toFixed(0)}px, 0, 0)`;
}

// requestAnimationFrame dentro del timer: si el compositor no esta pintando
// la ventana (tapada / minimizada) no se hace trabajo de mas.
// Giros de los widgets redondos y del reactor (2026-11-07): los mueve este
// bucle a 12 fps en vez de animaciones CSS (una animación CSS infinita hacía
// que WebKit repintara a 60 fps: ~3 veces más CPU). Periodo en s (negativo = al revés).
const SPIN_PERIOD = { "rg-spin": 14, slow: 40, mid: 18, rev: -11, fast: -7 };
function spinFrame(s) {
  if (power.saver) return;  // con batería: quietos
  for (const n of document.querySelectorAll(".rg-spin, .rx-rot")) {
    const per = n.classList.contains("rg-spin") ? SPIN_PERIOD["rg-spin"]
      : SPIN_PERIOD[["slow", "mid", "rev", "fast"].find((c) => n.classList.contains(c))] || 20;
    n.style.transform = `rotate(${(((s / per) % 1) * 360).toFixed(1)}deg)`;
  }
  const g = 0.575 - 0.325 * Math.cos((s / 3) * 2 * Math.PI);  // latido del núcleo, 3 s
  for (const n of document.querySelectorAll(".rx-glow")) n.style.opacity = g.toFixed(2);
}

let ambientSkip = 0;
setInterval(() => {
  if (ambient.pending || document.hidden || power.paused || power.still) return;
  if (power.saver && ambientSkip++ % 3) return;  // ~4 fps con batería
  ambient.pending = true;
  requestAnimationFrame(ambientFrame);
}, 1000 / AMBIENT_FPS);

// Parpadeos (punto de enlace, ">>" de la ruleta, alertas) con una sola clase.
setInterval(() => { if (!power.paused && !power.still) document.body.classList.toggle("bk"); }, 700);

function rebuildBackground() {
  makeGridTile();
  makePackets();
  makeHlines();
  makeFloor();
}
window.addEventListener("resize", () => { makePackets(); makeFloor(); syncTerm(); });

readColors();
rebuildBackground();

// Modo navegador (sin backend): datos de ejemplo para poder ver la pagina.
if (!window.webkit || !window.webkit.messageHandlers) {
  setTimeout(() => window.__init({ board: { theme: "red", themes: DEFAULT_THEME_BAR, panels: null }, apps: {}, seedApps: [] }), 50);
}
