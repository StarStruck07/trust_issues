/* Trust Issues: shared interactions (no framework).
   - lineup(): counterparties on a height chart (height = trust score); drag one to simulate a change (never saved)
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
  const LABEL = { RED: "Don't trust", AMBER: "Doubts", GREEN: "Trusted", CANNOT_VERIFY: "Can't tell", HELD: "On hold" };
  const UNSCORED = r => r === "CANNOT_VERIFY" || r === "HELD";
  window.CRD = { inr };

  // ------------------------------------------------------------------ the lineup
  // Each counterparty stands against a height chart: height = trust score, on the real scoring bands.
  // Drag a figure up or down to simulate a different score; ₹ at risk is recomputed with the configured
  // risk probabilities. Nothing is saved. Unidentified companies are blurred silhouettes with a "?".
  window.CRD.lineup = function (root, points, scale, opts = {}) {
    if (!root || !points || !points.length) return;
    root.innerHTML = '<div class="lu-wall"></div><div class="lu-plates"></div><div class="lu-tip"></div><div class="lu-bar"></div>';
    const wall = $(".lu-wall", root), plates = $(".lu-plates", root), tip = $(".lu-tip", root), bar = $(".lu-bar", root);
    const G = scale.green_min, A = scale.amber_min, P = scale.prob;
    const maxExp = Math.max(1, ...points.map(p => p.exposure || 0));
    // order: least trusted on the left, the unidentified last
    const order = p => (UNSCORED(p.rating) ? 1000 : p.score);
    const state = points.slice().sort((a, b) => order(a) - order(b))
      .map(p => ({ ...p, orig: { rating: p.rating, score: p.score, at_risk: p.at_risk }, sim: false }));
    const baseTotal = state.reduce((s, p) => s + (p.at_risk || 0), 0);
    const bandOf = s => (s >= G ? "GREEN" : s >= A ? "AMBER" : "RED");
    const H = () => wall.clientHeight;
    const yOf = s => (Math.max(0, Math.min(100, s)) / 100) * (H() - 70);   // leave room for the head
    const sOf = y => (y / (H() - 70)) * 100;

    function drawWall() {
      let html = "";
      const band = (cls, from, to, label) => `<div class="lu-band b-${cls}" style="bottom:${yOf(from)}px;height:${yOf(to) - yOf(from)}px"><em>${label}</em></div>`;
      html += band("RED", 0, A, "Don't trust") + band("AMBER", A, G, "Doubts") + band("GREEN", G, 100, "Trusted");
      for (let t = 0; t <= 100; t += 5) {
        const major = t % 10 === 0;
        html += `<div class="lu-line${major ? " major" : ""}" style="bottom:${yOf(t)}px"></div>`;
      }
      wall.innerHTML = html;
      let nums = "";
      for (let t = 0; t <= 100; t += 10) nums += `<div class="lu-num${t % 50 === 0 ? " major" : ""}" style="top:${H() - yOf(t)}px">${t}</div>`;
      root.querySelectorAll(".lu-nums").forEach(e => e.remove());
      wall.insertAdjacentHTML("beforebegin", `<div class="lu-nums" style="position:absolute;left:${wall.offsetLeft}px;top:0;height:${H()}px;width:0">${nums}</div>`);
    }

    function xOf(i) { const W = wall.clientWidth; return (W / state.length) * (i + .5); }
    function width(p) { return Math.round(54 + 46 * Math.sqrt((p.exposure || 0) / maxExp)); }

    function render(first) {
      state.forEach((p, i) => {
        if (!p._el) {
          p._el = document.createElement("div");
          p._el.className = "lu-suspect";
          p._el.innerHTML = '<div class="lu-fig"><span class="lu-score"></span></div>';
          p._el.tabIndex = 0; p._el.setAttribute("role", "slider");
          p._el.setAttribute("aria-valuemin", "0"); p._el.setAttribute("aria-valuemax", "100");
          wall.appendChild(p._el);
          p._plate = document.createElement("div");
          p._plate.className = "lu-plate";
          plates.appendChild(p._plate);
          p._plate.addEventListener("click", () => open(p));
          bind(p);
        }
        const unknown = UNSCORED(p.rating) && !p.sim;
        const h = unknown ? yOf(78) : Math.max(28, yOf(p.score));
        p._el.className = "lu-suspect r-" + p.rating + (p.sim ? " sim" : "");
        p._el.style.left = xOf(i) + "px";
        p._el.style.setProperty("--w", width(p) + "px");
        const fig = $(".lu-fig", p._el);
        if (first) { fig.style.height = "0px"; requestAnimationFrame(() => setTimeout(() => { fig.style.height = h + "px"; }, 120 + i * 110)); }
        else fig.style.height = h + "px";
        $(".lu-score", p._el).textContent = unknown ? "?" : Math.round(p.score);
        p._el.setAttribute("aria-valuenow", Math.round(p.score));
        p._el.setAttribute("aria-label", `${p.name}: ${LABEL[p.rating]}, trust score ${p.score}. Arrow keys simulate a change.`);
        p._plate.style.left = xOf(i) + "px";
        p._plate.innerHTML = `<b>${esc(p.name.length > 24 ? p.name.slice(0, 23) + "…" : p.name)}</b><span>${esc(p.cp_id)} · ${inr(p.exposure)}</span>`;
      });
      summary();
    }

    function summary() {
      const total = state.reduce((s, p) => s + (p.at_risk || 0), 0);
      const diff = total - baseTotal, sims = state.filter(p => p.sim);
      bar.innerHTML =
        `<span><span class="big">${inr(total)}</span> at risk${diff ? `<span class="delta ${diff > 0 ? "up" : "down"}">${diff > 0 ? "+" : "−"}${inr(Math.abs(diff))}</span>` : ""}</span>` +
        (sims.length
          ? `<span class="sim-note">What if: ${sims.map(p => esc(p.name) + " → " + LABEL[p.rating]).join(", ")}. Not saved.</span><button type="button" data-reset>Put them back</button>`
          : `<span>Figure width = how much they owe you. Grab a head and drag it.</span>`);
      const r = $("[data-reset]", bar);
      if (r) r.onclick = reset;
    }

    function reset() {
      state.forEach(p => { p.rating = p.orig.rating; p.score = p.orig.score; p.at_risk = p.orig.at_risk; p.sim = false; });
      render(false);
    }

    function simulate(p, s) {
      s = Math.round(Math.max(0, Math.min(100, s)));
      p.score = s; p.rating = bandOf(s);
      p.at_risk = Math.round((p.exposure || 0) * (P[p.rating] ?? 0.5));
      p.sim = !(p.rating === p.orig.rating && p.score === p.orig.score);
      if (!p.sim) p.at_risk = p.orig.at_risk;
    }

    function showTip(p) {
      const was = p.sim ? `<div class="k" style="color:var(--hi)">What if · really ${LABEL[p.orig.rating]}${UNSCORED(p.orig.rating) ? "" : " at " + p.orig.score}</div>` : "";
      tip.innerHTML = `<div class="k">${esc(p.role)} · ${esc(p.importance || "")} · priority ${esc(p.prio || "—")}</div>
        <h5>${esc(p.name)}</h5><span class="pill sm r-${p.rating}">${LABEL[p.rating]}</span>
        <span class="k" style="margin-left:8px">${inr(p.at_risk)} at risk of ${inr(p.exposure)}</span>${was}
        <p>${esc(p.reason)}</p><div class="hint">Click the name plate for the full file</div>`;
      const r = root.getBoundingClientRect(), e = $(".lu-fig", p._el).getBoundingClientRect();
      let left = e.left - r.left + e.width / 2 + 24;
      if (left + 330 > r.width) left = e.left - r.left - 344;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = Math.max(0, e.top - r.top - 30) + "px";
      tip.classList.add("on");
    }
    const hideTip = () => tip.classList.remove("on");

    function bind(p) {
      const el = p._el;
      let start = null, moved = false;
      el.addEventListener("pointerenter", () => showTip(p));
      el.addEventListener("pointerleave", () => { if (!start) hideTip(); });
      el.addEventListener("focus", () => showTip(p));
      el.addEventListener("blur", hideTip);
      el.addEventListener("keydown", ev => {
        if (ev.key === "Enter") return open(p);
        const step = { ArrowUp: 5, ArrowRight: 5, ArrowDown: -5, ArrowLeft: -5 }[ev.key];
        if (!step) return;
        ev.preventDefault();
        simulate(p, (UNSCORED(p.rating) && !p.sim ? 78 : p.score) + step);
        render(false); showTip(p);
      });
      el.addEventListener("pointerdown", ev => {
        start = { y: ev.clientY, h: $(".lu-fig", el).getBoundingClientRect().height };
        moved = false; el.setPointerCapture(ev.pointerId);
      });
      el.addEventListener("pointermove", ev => {
        if (!start) return;
        const dy = start.y - ev.clientY;
        if (!moved && Math.abs(dy) < 4) return;
        if (!moved) { moved = true; el.classList.add("drag"); }
        simulate(p, sOf(Math.max(0, start.h + dy)));
        el.className = "lu-suspect drag r-" + p.rating + (p.sim ? " sim" : "");
        $(".lu-fig", el).style.height = Math.max(28, yOf(p.score)) + "px";
        $(".lu-score", el).textContent = Math.round(p.score);
        summary(); showTip(p);
      });
      const end = () => {
        if (!start) return;
        start = null; el.classList.remove("drag");
        if (moved) render(false); else open(p);
      };
      el.addEventListener("pointerup", end);
      el.addEventListener("pointercancel", end);
    }

    function open(p) { hideTip(); if (opts.onOpen) opts.onOpen(p); else location.href = p.url; }

    drawWall(); render(true);
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { const els = state.map(p => p._el); drawWall(); els.forEach(e => wall.appendChild(e)); render(false); }, 120); });
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
      : '<li class="palette-empty">Nothing matches. Try a name, “approvals” or “trusted”.</li>';
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
