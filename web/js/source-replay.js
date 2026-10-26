// Replay source: same handler interface as source-live.js. Plays a recorded run off its feed
// clock. `getPos()` returns the current feed time in ms (the parent page owns the clock).
export async function loadReplay(runId) {
  const base = `runs/${runId}/`;
  const get = async (f) => (await fetch(base + f)).text();
  const lines = (t) => t.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const [manifest, frames, stream, askwhy, workflow, calls] = await Promise.all([
    get("manifest.json").then(JSON.parse), get("frames.jsonl").then(lines),
    get("stream.jsonl").then(lines), get("askwhy.json").then(JSON.parse),
    get("workflow.jsonl").then(lines), get("calls.jsonl").then(lines)]);
  return { manifest, frames, stream, askwhy, workflow, calls };
}

export function connectReplay(profile, run, h) {
  const { frames, stream } = run;
  let si = 0, fi = 0, ended = false;
  const mine = (m) => m.profile_id == null || m.profile_id === profile;
  const dispatch = (m) => {
    if (!mine(m)) return;
    const p = m.payload;
    if (m.type === "meta") h.onMeta && h.onMeta({ ...p, mode: "replay" });
    else if (m.type === "event") h.onEvent && h.onEvent(p);
    else if (m.type === "overlay") h.onOverlay && h.onOverlay({ ...p, source: { ...p.source, kind: "recorded" } });
    else if (m.type === "score") h.onScore && h.onScore(p);
    else if (m.type === "end") { ended = true; h.onEnd && h.onEnd(p); }
  };
  return {
    advance(pos) {
      for (;;) {
        const s = stream[si], f = frames[fi];
        const sOk = s && s.at_ms <= pos, fOk = f && f.at_ms <= pos;
        if (sOk && (!fOk || s.at_ms <= f.at_ms)) { si++; dispatch(s); }
        else if (fOk) { fi++; h.onFrame && h.onFrame(f); }
        else break;
      }
    },
    get ended() { return ended; },
    get endMs() { return frames.length ? frames[frames.length - 1].at_ms : 0; },
    close() {},
  };
}
