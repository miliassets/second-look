import { Clock, formatMs } from "./clock.js";
import { connectLive } from "./source-live.js";
import { connectReplay, loadReplay } from "./source-replay.js";
import { Pitch } from "./pitch.js";
import { Overlays } from "./overlays.js";
import { renderDetails } from "./panel.js";

const qs = new URLSearchParams(location.search);
const profile = qs.get("profile") || "analyst-en";
const transparent = qs.get("transparent") === "1";
const replay = qs.get("replay") === "1";
const runId = qs.get("run");
document.body.classList.toggle("transparent", transparent);
document.body.dataset.profile = profile;
const $ = (id) => document.getElementById(id);
const lang = profile.endsWith("-es") ? "es" : "en";
const mode = profile.startsWith("fan") ? "fan" : "analyst";
document.body.dataset.mode = mode;

const clock = new Clock();
let t = {}, pitch = null, overlays = null, meta = null, ended = false;
const events = {};

async function boot() {
  t = await (await fetch(`i18n/${lang}.json`)).json();
  $("synthetic").textContent = t.synthetic;
  $("logTitle").textContent = t.log;
  $("viewTitle").textContent = t[mode];
  $("status").textContent = t.waiting;
  $("askInput").placeholder = t.ask_placeholder; $("askBtn").textContent = t.ask;
  if (!replay) $("askForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const q = $("askInput").value.trim(); if (!q) return;
    $("askBtn").disabled = true;
    try {
      const r = await fetch("/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, profile_id: profile }) });
      const d = await r.json();
      const a = $("answer"); a.hidden = false; a.dataset.supported = String(!!d.supported);
      a.textContent = r.ok ? d.text : (d.detail || "error");
    } finally { $("askBtn").disabled = false; }
  };
  pitch = new Pitch($("pitch"));
  overlays = new Overlays($("log"), $("lower"), t);
  if (mode === "analyst") {
    overlays.onSelect = async (o) => {
      let data;
      if (replay) data = detailFromRun(o.story_id);
      else { const r = await fetch(`/api/story/${encodeURIComponent(o.story_id)}`); if (!r.ok) return; data = await r.json(); }
      if (!data) return;
      renderDetails($("details"), data, o, (id) => {
        const e = events[id]; if (!e || e.x == null) return;
        pitch.pulse(e.end_x != null && ["goal", "shot"].includes(e.type) ? e.end_x : e.x, e.end_x != null && ["goal", "shot"].includes(e.type) ? e.end_y : e.y);
      }, t);
    };
  }
  const handlers = {
    onMeta(m) { meta = m; overlays.labelFor = (ms) => formatMs(ms, m.periods); pitch.setMeta(m, mode === "analyst");
      $("home").textContent = m.home.short; $("away").textContent = m.away.short;
      $("home").style.borderColor = m.home.kit_color; $("away").style.borderColor = m.away.kit_color;
      const me = m.profiles.find((p) => p.profile_id === profile);
      if (mode === "fan" && me && me.followed_player_id) { pitch.followed = me.followed_player_id; $("pitch").dataset.followed = pitch.followed; }
      $("status").textContent = t.live; },
    onFrame(f) { clock.set(f.match_ms, f.clock); pitch.pushFrame(f); $("clock").textContent = f.clock; },
    onEvent(e) { events[e.event_id] = e; clock.set(e.match_ms, null); },
    onOverlay(o) { overlays.add(o); },
    onScore(s) { $("score").textContent = `${s.home} – ${s.away}`; },
    onEnd() { ended = true; $("status").textContent = t.ended; document.body.dataset.ended = "1"; },
  };
  if (replay) await startReplay(handlers); else connectLive(profile, handlers);
  requestAnimationFrame(loop);
}
let source = null, pos = 0, speedLabel = "", recorded = null;
function detailFromRun(storyId) {
  const w = recorded.workflow.find((x) => x.story_id === storyId);
  if (!w) return null;
  return {
    moment: w.moment, stories: w.stories, verdicts: w.verdicts,
    candidates: w.candidates.map((c) => ({ ...c, claims: c.claims.map((k) => { const rec = w.claims[k.claim_id] || {}; return { claim_id: k.claim_id, type: k.type, status: rec.status || "unknown", reason: rec.reason || "", value: rec.value, evidence_ids: rec.evidence_ids || [] }; }) })),
    calls: recorded.calls.filter((c) => c.moment_id === w.moment.moment_id),
  };
}
const bannerText = (m, t) => m.live_inference
  ? `${t.recorded} · ${m.recorded_at.slice(0, 8)} · ${m.provider} · ${(m.models_served || [m.model]).join(", ")} · ${t.real_calls}`
  : `${t.recorded} · ${m.recorded_at.slice(0, 8)} · ${t.not_live}`;
async function startReplay(handlers) {
  const run = await loadReplay(runId); recorded = run;
  const m = run.manifest;
  $("banner").hidden = false;
  $("banner").textContent = bannerText(m, t);
  $("synthetic").textContent = `${t.synthetic} · ${t.recorded_short}`;
  setupRecordedAsk(run);
  source = connectReplay(profile, run, handlers);
  pos = m.start_ms;
  const t0 = performance.now(), standalone = window.parent === window, sp = Number(qs.get("speed") || 10);
  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "replay-pos") { pos = e.data.ms; speedLabel = e.data.speed === 1 ? "" : `${e.data.speed}×`; }
    if (e.data && e.data.type === "replay-reset") location.reload();
  });
  setInterval(() => {
    if (standalone) pos = m.start_ms + (performance.now() - t0) * sp;
    source.advance(pos);
    $("speedLabel").textContent = standalone ? `${sp}×` : speedLabel;
  }, 30);
}
function setupRecordedAsk(run) {
  const qs_ = run.askwhy.filter((a) => a.profile_id === profile);
  const form = $("askForm"); form.innerHTML = "";
  const sel = document.createElement("select"); sel.id = "askSelect";
  for (const a of qs_) { const o = document.createElement("option"); o.value = a.overlay_id; o.textContent = a.question; sel.append(o); }
  const btn = document.createElement("button"); btn.id = "askBtn"; btn.type = "submit"; btn.textContent = t.ask;
  const note = document.createElement("div"); note.className = "ask-note"; note.textContent = t.recorded_questions_only;
  form.append(sel, btn, note);
  form.onsubmit = (ev) => {
    ev.preventDefault();
    const a = qs_.find((x) => x.overlay_id === sel.value); if (!a) return;
    const el = $("answer"); el.hidden = false; el.dataset.supported = String(a.supported); el.textContent = a.text;
  };
}
function loop() {
  const layers = overlays.render(clock.ms);
  if (!transparent) pitch.draw(layers);
  requestAnimationFrame(loop);
}
boot();
