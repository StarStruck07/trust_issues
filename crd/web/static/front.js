/* Trust Issues front page: chapters, things you can push around, pinned case files,
   a creed that lights up as you read, and the what-if line. Data comes from the latest run. */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = window.CRD.inr;
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const P = window.CRD_POINTS || [];
  const VERDICT = { RED: "Don't trust", AMBER: "Doubts", GREEN: "Trusted", CANNOT_VERIFY: "Can't tell", HELD: "On hold" };
  const LINE = {
    RED: ["We don't trust them.", "Act now: hold credit, line up a backup."],
    AMBER: ["Something's off.", "Watch closely. Tighten terms before it's a problem."],
    CANNOT_VERIFY: ["Who are you, really?", "We couldn't pin down which company this is, so we won't pretend to rate it."],
    HELD: ["Not saying yet.", "A source we needed was down. No green light on silence."],
    GREEN: ["Fine. For now.", "Healthy on fresh, cited evidence. We'll look again next week."],
  };

  // ------------------------------------------------------------- chapters rail
  const rail = $$(".fx-rail a");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) rail.forEach(a => a.classList.toggle("on", a.dataset.ch === e.target.dataset.chapter));
    }), { rootMargin: "-45% 0px -50% 0px" });
    $$("[data-chapter]").forEach(s => io.observe(s));
  }

  // ------------------------------------------------------------- things on the desk: drag them, they drift as you scroll
  const objs = $$("[data-drag]");
  objs.forEach((o, i) => {
    o.style.setProperty("--d", (300 + i * 140) + "ms");
    let start = null;
    o.addEventListener("pointerdown", e => {
      start = { x: e.clientX, y: e.clientY, dx: parseFloat(o.dataset.dx || 0), dy: parseFloat(o.dataset.dy || 0) };
      o.setPointerCapture(e.pointerId); o.classList.add("held");
    });
    o.addEventListener("pointermove", e => {
      if (!start) return;
      o.dataset.dx = start.dx + e.clientX - start.x; o.dataset.dy = start.dy + e.clientY - start.y;
      o.style.setProperty("--dx", o.dataset.dx + "px"); o.style.setProperty("--dy", o.dataset.dy + "px");
      o.style.setProperty("--tilt", Math.max(-14, Math.min(14, (e.clientX - start.x) / 12)) + "deg");
    });
    const end = () => { start = null; o.classList.remove("held"); o.style.setProperty("--tilt", "0deg"); };
    o.addEventListener("pointerup", end); o.addEventListener("pointercancel", end);
  });

  // ------------------------------------------------------------- the files: pinned scroller
  const files = $("#files"), frame = $("#fx-frame"), peek = $("#fx-peek"), cap = $("#fx-cap"), num = $("#fx-i");
  const order = { RED: 0, AMBER: 1, CANNOT_VERIFY: 2, HELD: 3, GREEN: 4 };
  const list = P.slice().sort((a, b) => (order[a.rating] - order[b.rating]) || (b.at_risk - a.at_risk)).slice(0, 7);
  function card(p, small) {
    return `<div class="cf r-${p.rating}${small ? " small" : ""}">
      <div class="cf-tab">FILE ${esc(p.cp_id)}</div>
      <div class="cf-top"><span class="cf-role">${esc(p.role)} · ${esc(p.importance || "")}</span><span class="pill r-${p.rating}">${VERDICT[p.rating]}</span></div>
      <div class="cf-name">${esc(p.name)}</div>
      <div class="cf-mid">
        <div class="cf-score"><b>${p.rating === "CANNOT_VERIFY" || p.rating === "HELD" ? "?" : p.score}</b><span>trust score</span></div>
        <div class="cf-money"><div><span>owed / exposed</span><b>${inr(p.exposure)}</b></div><div><span>at risk</span><b class="risk">${inr(p.at_risk)}</b></div></div>
      </div>
      <p class="cf-reason">${esc(p.reason)}</p>
      <div class="cf-open">open the file →</div>
    </div>`;
  }
  let cur = -1;
  function showFile(i) {
    if (i === cur || !list[i]) return;
    const dir = i > cur ? 1 : -1; cur = i;
    const p = list[i];
    frame.classList.remove("in"); cap.classList.remove("in");
    frame.style.setProperty("--from", dir * 60 + "px");
    void frame.offsetWidth;
    frame.innerHTML = card(p); frame.dataset.url = p.url;
    peek.innerHTML = list[i + 1] ? card(list[i + 1], true) : "";
    const [h, s] = LINE[p.rating] || ["", ""];
    cap.innerHTML = `<h2>${esc(h)}</h2><p>${esc(s)} <span class="dim">${esc(p.name)}: ${inr(p.at_risk)} at risk.</span></p>`;
    requestAnimationFrame(() => { frame.classList.add("in"); cap.classList.add("in"); });
    num.textContent = String(i + 1).padStart(2, "0");
  }
  if (files && list.length) {
    showFile(0);
    const open = () => window.CRD.openDossier(frame.dataset.url);
    frame.addEventListener("click", open);
    frame.addEventListener("keydown", e => { if (e.key === "Enter") open(); });
    // tilt toward the cursor, like a frame on a wall
    frame.addEventListener("pointermove", e => {
      const r = frame.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
      frame.style.setProperty("--rx", (-y * 6) + "deg"); frame.style.setProperty("--ry", (x * 8) + "deg");
    });
    frame.addEventListener("pointerleave", () => { frame.style.setProperty("--rx", "0deg"); frame.style.setProperty("--ry", "0deg"); });
  }

  // ------------------------------------------------------------- the creed: light words up as you scroll past
  const scrub = $("#fx-scrub");
  let words = [];
  if (scrub) {
    scrub.innerHTML = scrub.textContent.trim().split(/\s+/).map(w => `<span class="sw">${esc(w)}</span>`).join(" ");
    words = $$(".sw", scrub);
  }

  // ------------------------------------------------------------- one scroll loop for everything
  let ticking = false;
  function frameLoop() {
    ticking = false;
    const vh = innerHeight;
    // pinned files
    if (files && list.length) {
      const r = files.getBoundingClientRect();
      const span = files.offsetHeight - vh;
      const t = Math.max(0, Math.min(.9999, -r.top / Math.max(1, span)));
      showFile(Math.floor(t * list.length));
      files.style.setProperty("--t", t.toFixed(3));
    }
    // creed
    if (words.length) {
      const r = scrub.getBoundingClientRect();
      const t = Math.max(0, Math.min(1, (vh * .78 - r.top) / (r.height + vh * .35)));
      const lit = Math.round(t * words.length);
      words.forEach((w, i) => w.classList.toggle("lit", i < lit));
    }
    // objects drift at different speeds
    if (!still) objs.forEach((o, i) => o.style.setProperty("--py", (-scrollY * (.12 + (i % 3) * .07)).toFixed(1) + "px"));
  }
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(frameLoop); } };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onScroll);
  frameLoop();

  // ------------------------------------------------------------- what if
  const line = $("#riskline");
  if (line && P.length) window.CRD.riskLine(line, P, window.CRD_SCALE, { onOpen: p => window.CRD.openDossier(p.url) });

  // ------------------------------------------------------------- example strip: drag sideways, with a little momentum
  const strip = $("#fx-strip");
  if (strip) {
    let down = null, vx = 0, last = 0;
    strip.addEventListener("pointerdown", e => { if (e.pointerType !== "mouse" || e.target.closest("a")) return; down = { x: e.clientX, s: strip.scrollLeft }; last = e.clientX; vx = 0; strip.setPointerCapture(e.pointerId); strip.classList.add("dragging"); });
    strip.addEventListener("pointermove", e => { if (!down) return; strip.scrollLeft = down.s - (e.clientX - down.x); vx = e.clientX - last; last = e.clientX; });
    const up = () => {
      if (!down) return; down = null; strip.classList.remove("dragging");
      (function glide() { if (Math.abs(vx) < .5 || still) return; strip.scrollLeft -= vx; vx *= .92; requestAnimationFrame(glide); })();
    };
    strip.addEventListener("pointerup", up); strip.addEventListener("pointercancel", up);
    strip.addEventListener("wheel", e => { if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && e.shiftKey) { strip.scrollLeft += e.deltaY; e.preventDefault(); } }, { passive: false });
  }

  // ------------------------------------------------------------- entrance
  requestAnimationFrame(() => document.body.classList.add("ready"));
})();
