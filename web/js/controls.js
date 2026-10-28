// Control bar. Live mode: start feed, inject a correction. Static hosting (no backend): recorded
// run replay with a clock owned here and posted to both viewer iframes.
const $ = (id) => document.getElementById(id);
const state = $("state"), start = $("start"), correct = $("correct");

async function probe() {
  try { const r = await fetch("/api/state"); return r.ok; } catch { return false; }
}

async function liveMode() {
  $("analyst").src = "viewer.html?profile=analyst-en";
  $("fan").src = "viewer.html?profile=fan-es";
  async function poll() {
    try {
      const s = await (await fetch("/api/state")).json();
      state.textContent = !s.started ? "feed not started" : s.ended ? "feed ended" : `live · speed ${s.speed} · llm ${s.llm}`;
      start.hidden = s.started; correct.hidden = !s.started;
      document.body.dataset.ended = s.ended ? "1" : "0";
    } catch { state.textContent = "offline"; }
  }
  start.onclick = async () => { await fetch("/control/start", { method: "POST" }); poll(); };
  correct.onclick = async () => { correct.disabled = true; const r = await fetch("/control/correction?kind=void_goal", { method: "POST" }); if (!r.ok) alert((await r.json()).detail); correct.disabled = false; };
  poll(); setInterval(poll, 1500);
}

async function replayMode() {
  const idx = await (await fetch("runs/index.json")).json();
  const runId = new URLSearchParams(location.search).get("run") || idx.default;
  const info = idx.runs.find((r) => r.run_id === runId) || {};
  const man = await (await fetch(`runs/${runId}/manifest.json`)).json();
  document.body.dataset.mode = "replay";
  state.textContent = man.live_inference
    ? `Recorded agent run · ${man.recorded_at.slice(0, 8)} · ${man.provider} · ${(man.models_served || [man.model]).join(", ")} · real model calls`
    : `Recorded agent run · ${man.recorded_at.slice(0, 8)} · not live inference`;
  $("replayBar").hidden = false;
  $("recordedNote").textContent = info.correction_at ? `Recorded: correction at ${info.correction_at}` : "Recorded run";
  correct.hidden = true; start.hidden = true;
  const frames = [$("analyst"), $("fan")];
  frames[0].src = `viewer.html?profile=analyst-en&replay=1&run=${runId}`;
  frames[1].src = `viewer.html?profile=fan-es&replay=1&run=${runId}`;
  let speed = 10, pos = man.start_ms, last = performance.now();
  const post = () => frames.forEach((f) => f.contentWindow && f.contentWindow.postMessage({ type: "replay-pos", ms: pos, speed }, "*"));
  setInterval(() => { const now = performance.now(); pos += (now - last) * speed; last = now; if (pos > man.end_ms + 5000) pos = man.end_ms + 5000; post(); document.body.dataset.pos = String(Math.round(pos)); }, 33);
  document.querySelectorAll("#replayBar [data-speed]").forEach((b) => { b.onclick = () => { speed = Number(b.dataset.speed); document.body.dataset.speed = String(speed); }; });
  $("restart").onclick = () => { pos = man.start_ms; frames.forEach((f) => f.contentWindow.postMessage({ type: "replay-reset" }, "*")); };
  document.body.dataset.speed = String(speed);
}

(async () => { if (await probe()) await liveMode(); else await replayMode(); })();
