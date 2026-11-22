/* The Counterparty Desk: shared interactions (no framework).
   - riskLine(): counterparties on a 0-100 line; drag one to simulate a rating change (never saved)
   - command palette (Ctrl/Cmd+K), dossier drawer for /cp/ links, desk keyboard nav, scroll reveals */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ₹ in Indian units, matching the report: ₹25,000 · ₹2.1 L · ₹1.2 Cr
  function inr(v) {
    v = Number(v) || 0;
    if (v >= 1e7) return "₹" + (v / 1e7).toFixed(v >= 1e8 ? 0 : 1).replace(/\.0$/, "") + " Cr";
    if (v >= 1e5) return "₹" + (v / 1e5).toFixed(1).replace(/\.0$/, "") + " L";
    return "₹" + Math.round(v).toLocaleString("en-IN");
  }
  const LABEL = { RED: "Red", AMBER: "Amber", GREEN: "Green", CANNOT_VERIFY: "Cannot verify", HELD: "Held" };
  const UNSCORED = r => r === "CANNOT_VERIFY" || r === "HELD";
  window.CRD = { inr };

  // ------------------------------------------------------------------ risk line
  window.CRD.riskLine = function (root, points, scale, opts = {}) {
    if (!root || !points) return;
    const tray = points.some(p => UNSCORED(p.rating));
    const trayW = tray ? (opts.trayW || 170) : 0;
    root.style.setProperty("--tray-w", trayW + "px");
    root.innerHTML = '<div class="rl-track"></div><div class="rl-tip"></div><div class="rl-bar"></div>';
    const track = $(".rl-track", root), tip = $(".rl-tip", root), bar = $(".rl-bar", root);
    const G = scale.green_min, A = scale.amber_min, P = scale.prob;
    const maxExp = Math.max(1, ...points.map(p => p.exposure || 0));
    const state = points.map(p => ({ ...p, orig: { rating: p.rating, score: p.score, at_risk: p.at_risk }, sim: false }));
    const baseTotal = state.reduce((s, p) => s + (p.at_risk || 0), 0);
    const bandOf = s => (s >= G ? "GREEN" : s >= A ? "AMBER" : "RED");

    function geom() {
      const W = track.clientWidth, pad = 14;
      const scaleW = W - trayW - pad * 2;
      return { W, pad, scaleW, x: s => pad + (Math.max(0, Math.min(100, s)) / 100) * scaleW, s: x => ((x - pad) / scaleW) * 100 };
    }

    function drawStatic() {
      const g = geom();
      const z = (cls, from, to, title, sub) =>
        `<div class="rl-zone z-${cls}" style="left:${g.x(from)}px;width:${g.x(to) - g.x(from)}px"><span><b>${title}</b>${sub}</span></div>`;
      let html = z("RED", 0, A, "Red", `0–${A - 1} · act now`) + z("AMBER", A, G, "Amber", `${A}–${G - 1} · watch`) +
        z("GREEN", G, 100, "Green", `${G}–100 · clear`) + '<div class="rl-axis"></div>';
      for (let t = 0; t <= 100; t += 10) html += `<div class="rl-tick" style="left:${g.x(t)}px"><i>${t}</i></div>`;
      if (tray) html += `<div class="rl-tray"><span><b>Won't guess</b>cannot verify · held</span></div>`;
      track.innerHTML = html;
    }

    function size(p) { return Math.round(26 + 34 * Math.sqrt((p.exposure || 0) / maxExp)); }

    function layout() {
      const g = geom();
      const base = 34 + 30; // above the axis labels
      const scored = state.filter(p => !(UNSCORED(p.rating) && !p.sim)).sort((a, b) => a.score - b.score);
      const lanes = [];
      scored.forEach(p => {
        const x = g.x(p.score), d = size(p);
        let lane = 0;
        while (lanes[lane] !== undefined && x - lanes[lane] < d + 70) lane++;
        lanes[lane] = x;
        p._x = x; p._y = base + lane * 74 + d / 2;
      });
      const tx = g.W - trayW / 2;
      state.filter(p => UNSCORED(p.rating) && !p.sim).forEach((p, i) => { p._x = tx; p._y = base + i * 74 + size(p) / 2; });
      const h = Math.max(230, ...state.map(p => p._y + size(p) / 2 + 40));
      track.style.height = h + "px";
    }

    function render(animate) {
      layout();
      state.forEach((p, i) => {
        let el = p._el;
        if (!el) {
          el = p._el = document.createElement("div");
          el.className = "tok" + (animate ? " in" : "");
          if (animate) el.style.animationDelay = (120 + i * 90) + "ms";
          el.tabIndex = 0;
          el.setAttribute("role", "button");
          track.appendChild(el);
          bind(el, p);
        }
        const d = size(p);
        el.className = el.className.replace(/\br-\w+/g, "").trim() + " r-" + p.rating + (p.sim ? " sim" : "");
        Object.assign(el.style, { left: p._x + "px", bottom: p._y + "px", width: d + "px", height: d + "px" });
        el.textContent = UNSCORED(p.rating) && !p.sim ? "?" : Math.round(p.score);
        el.dataset.label = p.name.length > 22 ? p.name.slice(0, 21) + "…" : p.name;
        el.setAttribute("aria-label", `${p.name}: ${LABEL[p.rating]}, score ${p.score}`);
      });
      summary();
    }

    function summary() {
      const total = state.reduce((s, p) => s + (p.at_risk || 0), 0);
      const diff = total - baseTotal, sims = state.filter(p => p.sim);
      const by = r => state.filter(p => (r === "CV" ? UNSCORED(p.rating) : p.rating === r)).length;
      bar.innerHTML =
        `<span><b>${inr(total)}</b> at risk${diff ? `<span class="delta ${diff > 0 ? "up" : "down"}">${diff > 0 ? "▲" : "▼"} ${inr(Math.abs(diff))}</span>` : ""}</span>` +
        `<span>${by("RED")} red · ${by("AMBER")} amber · ${by("GREEN")} green · ${by("CV")} unverified</span>` +
        `<span>size = exposure</span>` +
        (sims.length ? `<span class="sim-note">What-if: ${sims.map(p => esc(p.name) + " → " + LABEL[p.rating]).join(", ")}. Not saved.</span> <button type="button" data-reset>Reset</button>`
          : `<span class="faint">Drag a counterparty to ask “what if?”</span>`);
      const r = $("[data-reset]", bar);
      if (r) r.onclick = reset;
      if (opts.onChange) opts.onChange(state, total);
    }

    function reset() {
      state.forEach(p => { p.rating = p.orig.rating; p.score = p.orig.score; p.at_risk = p.orig.at_risk; p.sim = false; });
      render(false);
    }

    function showTip(p, el) {
      const sim = p.sim ? `<div class="k" style="color:#e8c98a">simulated · was ${LABEL[p.orig.rating]} ${p.orig.score}</div>` : "";
      tip.innerHTML = `<div class="k">${esc(p.role)} · ${esc(p.importance || "")} · ${esc(p.prio || "")}</div>
        <h5>${esc(p.name)}</h5><span class="pill sm r-${p.rating}">${LABEL[p.rating]}</span>
        <span class="k" style="margin-left:8px">score ${Math.round(p.score)} · ${inr(p.at_risk)} at risk of ${inr(p.exposure)}</span>${sim}
        <p>${esc(p.orig.rating === p.rating ? p.reason : "Simulated: real rating is " + LABEL[p.orig.rating] + ". " + p.reason)}</p>
        <div class="k" style="margin-top:8px">click to open the dossier · drag to simulate</div>`;
      const r = root.getBoundingClientRect(), e = el.getBoundingClientRect();
      let left = e.left - r.left + e.width / 2 - 145;
      left = Math.max(0, Math.min(left, r.width - 290));
      tip.style.left = left + "px";
      tip.style.top = Math.max(0, e.top - r.top - tip.offsetHeight - 14) + "px";
      tip.classList.add("on");
    }
    const hideTip = () => tip.classList.remove("on");

    function bind(el, p) {
      let start = null, moved = false, ghost = null;
      el.addEventListener("pointerenter", () => showTip(p, el));
      el.addEventListener("pointerleave", () => { if (!start) hideTip(); });
      el.addEventListener("focus", () => showTip(p, el));
      el.addEventListener("blur", hideTip);
      el.addEventListener("keydown", ev => {
        if (ev.key === "Enter") open(p);
        if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") { ev.preventDefault(); simulate(p, (p.sim || !UNSCORED(p.rating) ? p.score : 50) + (ev.key === "ArrowLeft" ? -5 : 5)); render(false); showTip(p, el); }
      });
      el.addEventListener("pointerdown", ev => {
        start = { x: ev.clientX, left: parseFloat(el.style.left) };
        moved = false;
        el.setPointerCapture(ev.pointerId);
      });
      el.addEventListener("pointermove", ev => {
        if (!start) return;
        const dx = ev.clientX - start.x;
        if (!moved && Math.abs(dx) < 4) return;
        if (!moved) {
          moved = true; el.classList.add("drag");
          ghost = document.createElement("div"); ghost.className = "rl-ghost";
          ghost.style.left = start.left + "px"; ghost.style.height = (parseFloat(el.style.bottom) - 34) + "px";
          track.appendChild(ghost);
        }
        const g = geom();
        const x = Math.max(g.pad, Math.min(g.pad + g.scaleW, start.left + dx));
        el.style.left = x + "px";
        simulate(p, g.s(x));
        el.className = el.className.replace(/\br-\w+/g, "").trim() + " r-" + p.rating;
        el.textContent = Math.round(p.score);
        summary(); showTip(p, el);
      });
      const end = () => {
        if (!start) return;
        start = null; el.classList.remove("drag");
        if (ghost) { ghost.remove(); ghost = null; }
        if (moved) render(false); else open(p);
      };
      el.addEventListener("pointerup", end);
      el.addEventListener("pointercancel", end);
    }

    function simulate(p, score) {
      score = Math.max(0, Math.min(100, score));
      p.score = Math.round(score);
      p.rating = bandOf(score);
      p.at_risk = Math.round((p.exposure || 0) * (P[p.rating] ?? 0.5));
      p.sim = !(p.rating === p.orig.rating && p.score === p.orig.score);
      if (!p.sim) p.at_risk = p.orig.at_risk;
    }

    function open(p) { hideTip(); if (opts.onOpen) opts.onOpen(p); else location.href = p.url; }

    drawStatic(); render(true);
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { drawStatic(); state.forEach(p => { if (p._el) track.appendChild(p._el); }); render(false); }, 120); });
    return { reset, state };
  };

  // ------------------------------------------------------------------ dossier drawer
  const drawer = $("#drawer"), dbody = $("#drawer-body"), dfull = $("#drawer-full");
  async function openDossier(url) {
    if (!drawer) { location.href = url; return; }
    drawer.hidden = false;
    dfull.href = url;
    dbody.innerHTML = '<p class="drawer-loading">Pulling the file…</p>';
    try {
      const html = await (await fetch(url, { headers: { "HX-Request": "dossier" } })).text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const main = doc.querySelector("main");
      dbody.innerHTML = main ? main.innerHTML : html;
      dbody.scrollTop = 0;
    } catch (e) {
      dbody.innerHTML = `<p class="drawer-loading">Could not load. <a href="${esc(url)}">Open the full page</a>.</p>`;
    }
  }
  function closeDossier() { if (drawer) drawer.hidden = true; }
  window.CRD.openDossier = openDossier;
  if (drawer) {
    $("#drawer-close").addEventListener("click", closeDossier);
    document.addEventListener("click", ev => {
      const a = ev.target.closest && ev.target.closest('a[href^="/cp/"]');
      if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0 || a.target === "_blank") return;
      if (location.pathname.startsWith("/cp/")) return;
      ev.preventDefault();
      openDossier(a.getAttribute("href"));
    });
  }

  // ------------------------------------------------------------------ command palette
  const pal = $("#palette"), pq = $("#palette-q"), plist = $("#palette-list");
  let items = [], shown = [], cur = 0, loaded = false;
  const PAGES = [
    { kind: "page", label: "Front page", hint: "this week at a glance", go: "/" },
    { kind: "page", label: "Desk", hint: "ranked counterparties", go: "/desk" },
    { kind: "page", label: "Approvals", hint: "drafts waiting for a human", go: "/approvals" },
    { kind: "page", label: "Outbox", hint: "what was actually sent", go: "/outbox" },
    { kind: "page", label: "Ledger", hint: "every call and its cost", go: "/ledger" },
    { kind: "page", label: "Runs", hint: "past reviews", go: "/runs" },
    { kind: "command", label: "New review…", hint: "ask the desk", go: "/desk#ask" },
    { kind: "command", label: "Download the ledger (CSV)", hint: "all runs", go: "/ledger.csv" },
  ];
  async function loadItems() {
    if (loaded) return;
    loaded = true;
    items = PAGES.slice();
    let pts = window.CRD_POINTS, rid = window.CRD_RUN;
    if (!pts) {
      try { const j = await (await fetch("/api/points")).json(); pts = j.points; rid = j.run_id; } catch (e) { pts = []; }
    }
    if (rid) items.push({ kind: "command", label: "Open this week's report", hint: rid, go: `/runs/${rid}/report` });
    (pts || []).forEach(p => items.push({ kind: "counterparty", label: p.name, hint: `${p.cp_id} · ${p.role}`, rating: p.rating, go: p.url, dossier: true }));
    draw();
  }
  function score(it, q) {
    if (!q) return 1;
    const hay = (it.label + " " + (it.hint || "") + " " + it.kind + " " + (it.rating || "")).toLowerCase();
    return q.toLowerCase().split(/\s+/).every(w => hay.includes(w)) ? (it.label.toLowerCase().startsWith(q.toLowerCase()) ? 2 : 1) : 0;
  }
  function draw() {
    const q = pq.value.trim();
    shown = items.map(it => [score(it, q), it]).filter(x => x[0]).sort((a, b) => b[0] - a[0]).map(x => x[1]).slice(0, 40);
    cur = Math.min(cur, Math.max(0, shown.length - 1));
    plist.innerHTML = shown.length ? shown.map((it, i) =>
      `<li class="${i === cur ? "on" : ""}" data-i="${i}"><span class="kind">${it.kind}</span><span class="main">${esc(it.label)}<small>${esc(it.hint || "")}</small></span>${it.rating ? `<span class="pill sm r-${it.rating}">${LABEL[it.rating]}</span>` : ""}</li>`).join("")
      : '<li class="palette-empty">Nothing matches. Try a name, “approvals” or “red”.</li>';
    const on = $("li.on", plist); if (on) on.scrollIntoView({ block: "nearest" });
  }
  function go(it) {
    if (!it) return;
    closePalette();
    if (it.go === "/desk#ask" && $("#ask-text")) { $("#ask-text").focus(); $("#ask-text").scrollIntoView({ behavior: "smooth", block: "center" }); return; }
    if (it.dossier && drawer && !location.pathname.startsWith("/cp/")) openDossier(it.go); else location.href = it.go;
  }
  function openPalette() { if (!pal) return; pal.hidden = false; pq.value = ""; cur = 0; loadItems(); draw(); setTimeout(() => pq.focus(), 0); }
  function closePalette() { if (pal) pal.hidden = true; }
  if (pal) {
    $$("[data-palette-open]").forEach(b => b.addEventListener("click", openPalette));
    pal.addEventListener("click", ev => { if (ev.target === pal) closePalette(); const li = ev.target.closest("li[data-i]"); if (li) go(shown[+li.dataset.i]); });
    pq.addEventListener("input", () => { cur = 0; draw(); });
    pq.addEventListener("keydown", ev => {
      if (ev.key === "ArrowDown") { ev.preventDefault(); cur = Math.min(cur + 1, shown.length - 1); draw(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); cur = Math.max(cur - 1, 0); draw(); }
      else if (ev.key === "Enter") { ev.preventDefault(); go(shown[cur]); }
    });
  }

  // ------------------------------------------------------------------ global keys
  const typing = el => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
  document.addEventListener("keydown", ev => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); pal && pal.hidden ? openPalette() : closePalette(); return; }
    if (ev.key === "Escape") { if (pal && !pal.hidden) closePalette(); else closeDossier(); return; }
    if (typing(document.activeElement)) return;
    const rows = $$("#ranked tbody tr.rated:not(.hidden)");
    if (!rows.length) return;
    let i = rows.findIndex(r => r.classList.contains("sel"));
    if (ev.key === "j" || ev.key === "k") {
      i = ev.key === "j" ? Math.min(i + 1, rows.length - 1) : Math.max(i - 1, 0);
      rows.forEach(r => r.classList.remove("sel")); rows[i].classList.add("sel"); rows[i].scrollIntoView({ block: "nearest" });
    } else if (ev.key === "Enter" && i >= 0) {
      const a = $('a[href^="/cp/"]', rows[i]); if (a) openDossier(a.getAttribute("href"));
    } else if (ev.key === "/") {
      const s = $("#desk-search"); if (s) { ev.preventDefault(); s.focus(); }
    }
  });

  // ------------------------------------------------------------------ reveal on scroll
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("seen"); io.unobserve(e.target); } }), { threshold: .12 });
    $$(".reveal").forEach(el => io.observe(el));
  } else $$(".reveal").forEach(el => el.classList.add("seen"));

  // ------------------------------------------------------------------ ask the desk
  const ask = $("#ask-text");
  if (ask) {
    $$("[data-ask]").forEach(b => b.addEventListener("click", () => { ask.value = b.dataset.ask; ask.focus(); }));
    ask.addEventListener("keydown", ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ask.form.requestSubmit(); } });
    const ex = $$("[data-ask]").map(b => b.dataset.ask);
    if (ex.length && !ask.value) {
      let k = 0, j = 0, del = false;
      const tick = () => {
        if (document.activeElement === ask || ask.value) { ask.placeholder = "Ask the desk…"; return setTimeout(tick, 1200); }
        const s = ex[k % ex.length];
        j += del ? -2 : 1;
        ask.placeholder = s.slice(0, Math.max(0, j)) + (del ? "" : "▍");
        if (!del && j >= s.length) { del = true; return setTimeout(tick, 1600); }
        if (del && j <= 0) { del = false; k++; }
        setTimeout(tick, del ? 18 : 42);
      };
      tick();
    }
    if (location.hash === "#ask") setTimeout(() => { ask.focus(); ask.scrollIntoView({ block: "center" }); }, 50);
  }
})();
