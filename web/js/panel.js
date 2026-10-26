// Analyst workflow panel: scouts -> Editor verdict -> checker results, plus clickable evidence.
const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

export function renderDetails(el, data, overlay, onEvidence, t) {
  el.hidden = false; el.innerHTML = "";
  el.dataset.story = overlay.story_id;
  el.append(h("h2", null, t.workflow));
  const scouts = h("div", "wf-scouts");
  for (const c of data.candidates) {
    const box = h("div", `cand cand-${c.status}`); box.dataset.candidate = c.candidate_id;
    box.append(h("b", null, `${c.scout} · ${c.thesis_tag}`), h("span", "cand-status", c.status));
    box.append(h("div", "cand-thesis", c.thesis));
    for (const k of c.claims) {
      const row = h("div", `claim claim-${k.status}`, `${k.status === "verified" ? "✓" : "✗"} ${k.type}${k.reason ? " — " + k.reason : ""}`);
      box.append(row);
    }
    scouts.append(box);
  }
  el.append(scouts);
  const v = data.verdicts[data.verdicts.length - 1];
  if (v) {
    const box = h("div", "verdict"); box.append(h("b", null, t.verdict));
    for (const r of v.rejected) box.append(h("div", "rej", `✗ ${r.candidate_id.split(".").pop()}: ${r.reason}`));
    for (const n of v.notes) box.append(h("div", "note", n));
    el.append(box);
  }
  const calls = data.calls.filter((c) => c.outcome);
  if (calls.length) {
    const box = h("div", "calls"); box.append(h("b", null, t.calls));
    for (const c of calls) box.append(h("div", "call", `${c.agent} · ${c.ms} ms · ${c.outcome}${c.cached ? " · cached" : ""}`));
    el.append(box);
  }
  const ev = h("div", "evidence"); ev.append(h("b", null, t.evidence));
  for (const id of overlay.evidence_ids || []) {
    const b = h("button", "ev", id.replace(/^m001-/, "")); b.dataset.eventId = id; b.type = "button";
    b.onclick = () => onEvidence(id);
    ev.append(b);
  }
  el.append(ev);
}
