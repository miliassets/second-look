// Overlay manager: shows an overlay while clock is inside [show_at, show_at + duration];
// `supersedes` removes the old one at once. Keeps a story timeline log.
export class Overlays {
  constructor(logEl, lowerEl, t) {
    this.byId = new Map(); this.logEl = logEl; this.lowerEl = lowerEl; this.t = t;
    this.superseded = new Set(); this.shownId = null; this.labelFor = null; this.onSelect = null;
  }
  add(o) {
    this.byId.set(o.overlay_id, o);
    if (o.supersedes) {
      this.superseded.add(o.supersedes);
      const old = this.logEl.querySelector(`[data-overlay-id="${CSS.escape(o.supersedes)}"]`);
      if (old) old.classList.add("superseded");
    }
    const li = document.createElement("li");
    li.dataset.overlayId = o.overlay_id; li.dataset.kind = o.kind; li.dataset.story = o.story_id;
    li.dataset.revision = o.revision; if (o.supersedes) li.dataset.supersedes = o.supersedes;
    li.className = `log-${o.kind}`;
    if (this.onSelect && ["explanation", "recap", "correction_notice"].includes(o.kind)) {
      li.classList.add("clickable"); li.tabIndex = 0;
      li.onclick = () => this.onSelect(o);
    }
    const clock = document.createElement("span"); clock.className = "log-clock"; clock.textContent = this.labelFor ? this.labelFor(o.show_at_match_ms) : "";
    const text = document.createElement("span"); text.className = "log-text"; text.textContent = `${o.headline ? o.headline + " — " : ""}${o.text}`;
    li.append(clock, text);
    for (const b of this.badgesFor(o)) { const s = document.createElement("span"); s.className = "badge"; s.textContent = b; li.append(s); }
    this.logEl.prepend(li);
  }
  badgesFor(o) { return [...(o.badges || []), ...(o.supersedes ? [this.t.updated] : [])]; }
  active(ms) {
    return [...this.byId.values()].filter((o) => !this.superseded.has(o.overlay_id) &&
      ms >= o.show_at_match_ms && ms < o.show_at_match_ms + o.duration_ms);
  }
  render(ms) {
    const act = this.active(ms).filter((o) => o.slot === "lower_third").sort((a, b) => b.priority - a.priority || b.show_at_match_ms - a.show_at_match_ms);
    const o = act[0];
    if (!o) { this.lowerEl.hidden = true; this.shownId = null; return []; }
    if (this.shownId !== o.overlay_id) {
      this.shownId = o.overlay_id;
      this.lowerEl.hidden = false;
      this.lowerEl.dataset.overlayId = o.overlay_id;
      this.lowerEl.dataset.kind = o.kind;
      this.lowerEl.innerHTML = "";
      const h = document.createElement("div"); h.className = "lt-head"; h.textContent = o.headline;
      const p = document.createElement("div"); p.className = "lt-text"; p.textContent = o.text;
      this.lowerEl.append(h, p);
      for (const b of this.badgesFor(o)) { const s = document.createElement("span"); s.className = "badge"; s.textContent = b; h.append(s); }
    }
    return this.active(ms).flatMap((x) => x.layers || []);
  }
}
