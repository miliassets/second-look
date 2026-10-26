// Canvas 2D pitch, 105 x 68 m. Players are dots in invented kit colours; layers draw on top.
const L = 105, W = 68, S = 10, PAD = 20;

export class Pitch {
  constructor(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d");
    canvas.width = L * S + PAD * 2; canvas.height = W * S + PAD * 2;
    this.prev = null; this.next = null; this.tNext = 0; this.gap = 500;
    this.kits = {}; this.team = {}; this.num = {}; this.showNumbers = false;
    this.followed = null; this.pulses = []; this.pulseCount = 0;
  }
  setMeta(meta, showNumbers) {
    this.kits[meta.home.team_id] = meta.home.kit_color;
    this.kits[meta.away.team_id] = meta.away.kit_color;
    for (const p of meta.players) { this.team[p.player_id] = p.team_id; this.num[p.player_id] = p.number; }
    this.showNumbers = showNumbers;
  }
  pulse(x, y) {
    this.pulses.push({ x, y, t0: performance.now() });
    this.pulseCount += 1; this.c.dataset.pulses = String(this.pulseCount);
  }
  pushFrame(f) {
    const now = performance.now();
    this.gap = this.next ? Math.max(16, now - this.tNext) : 500;
    this.prev = this.next || f; this.next = f; this.tNext = now;
  }
  pos(id) {
    // interpolate between the last two frames
    if (!this.next) return null;
    const a = Math.min(1, (performance.now() - this.tNext) / Math.max(this.gap, 16));
    const n = this.next.players.find((p) => p[0] === id);
    if (!n) return null;
    const p = this.prev.players.find((q) => q[0] === id) || n;
    return [p[1] + (n[1] - p[1]) * a, p[2] + (n[2] - p[2]) * a];
  }
  X(x) { return PAD + x * S; }
  Y(y) { return PAD + (W - y) * S; }
  draw(layers) {
    const g = this.ctx;
    g.clearRect(0, 0, this.c.width, this.c.height);
    this.drawPitch(g);
    for (const ly of layers) this.drawLayer(g, ly);
    const now = performance.now();
    this.pulses = this.pulses.filter((p) => now - p.t0 < 2500);
    for (const p of this.pulses) {
      const k = (now - p.t0) / 2500;
      g.beginPath(); g.arc(this.X(p.x), this.Y(p.y), 10 + 34 * k, 0, 7);
      g.strokeStyle = `rgba(255,225,77,${1 - k})`; g.lineWidth = 4; g.stroke();
    }
    if (!this.next) return;
    for (const p of this.next.players) {
      const q = this.pos(p[0]);
      if (!q) continue;
      g.beginPath(); g.arc(this.X(q[0]), this.Y(q[1]), 7, 0, 7);
      g.fillStyle = this.kits[this.team[p[0]]] || "#888"; g.fill();
      g.lineWidth = 1.5; g.strokeStyle = "rgba(255,255,255,.85)"; g.stroke();
      if (this.showNumbers) {
        g.fillStyle = "#fff"; g.font = "bold 8px sans-serif"; g.textAlign = "center";
        g.fillText(String(this.num[p[0]] ?? ""), this.X(q[0]), this.Y(q[1]) + 3);
      }
    }
    if (this.followed) {
      const q = this.pos(this.followed);
      if (q) { g.beginPath(); g.arc(this.X(q[0]), this.Y(q[1]), 12, 0, 7); g.strokeStyle = "#ffe14d"; g.lineWidth = 3; g.setLineDash([4, 3]); g.stroke(); g.setLineDash([]); }
    }
    const b = this.next.ball;
    g.beginPath(); g.arc(this.X(b[0]), this.Y(b[1]), 4.5, 0, 7);
    g.fillStyle = "#fff"; g.fill(); g.lineWidth = 1; g.strokeStyle = "#222"; g.stroke();
  }
  drawPitch(g) {
    g.fillStyle = "#2f7d3f"; g.fillRect(0, 0, this.c.width, this.c.height);
    for (let i = 0; i < 12; i++) { g.fillStyle = i % 2 ? "rgba(255,255,255,.04)" : "rgba(0,0,0,.04)"; g.fillRect(PAD + i * (L * S / 12), PAD, L * S / 12, W * S); }
    g.strokeStyle = "rgba(255,255,255,.85)"; g.lineWidth = 2;
    g.strokeRect(PAD, PAD, L * S, W * S);
    g.beginPath(); g.moveTo(this.X(L / 2), this.Y(0)); g.lineTo(this.X(L / 2), this.Y(W)); g.stroke();
    g.beginPath(); g.arc(this.X(L / 2), this.Y(W / 2), 9.15 * S, 0, 7); g.stroke();
    for (const x of [0, L - 16.5]) g.strokeRect(this.X(x), this.Y(W / 2 + 20.16), 16.5 * S, 40.32 * S);
    for (const x of [0, L - 5.5]) g.strokeRect(this.X(x), this.Y(W / 2 + 9.16), 5.5 * S, 18.32 * S);
  }
  drawLayer(g, ly) {
    g.save();
    if (ly.type === "zone") {
      const [x, y, w, h] = ly.rect;
      g.fillStyle = "rgba(255,230,80,.18)"; g.fillRect(this.X(x), this.Y(y + h), w * S, h * S);
    } else if (ly.type === "chain") {
      g.strokeStyle = "rgba(255,240,120,.95)"; g.fillStyle = "rgba(255,240,120,.95)"; g.lineWidth = 3;
      g.beginPath();
      ly.points.forEach((p, i) => (i ? g.lineTo(this.X(p[0]), this.Y(p[1])) : g.moveTo(this.X(p[0]), this.Y(p[1]))));
      g.stroke();
      ly.points.forEach((p, i) => { g.font = "bold 10px sans-serif"; g.textAlign = "center"; g.fillText(String(i + 1), this.X(p[0]), this.Y(p[1]) - 8); });
    } else if (ly.type === "marker") {
      g.beginPath(); g.arc(this.X(ly.at[0]), this.Y(ly.at[1]), 11, 0, 7);
      g.fillStyle = "rgba(255,90,60,.9)"; g.fill(); g.strokeStyle = "#fff"; g.lineWidth = 2; g.stroke();
      if (ly.label) { g.fillStyle = "#fff"; g.font = "bold 11px sans-serif"; g.textAlign = "center"; g.fillText(ly.label, this.X(ly.at[0]), this.Y(ly.at[1]) + 4); }
    } else if (ly.type === "player_halo") {
      const q = this.pos(ly.player_id);
      if (q) { g.beginPath(); g.arc(this.X(q[0]), this.Y(q[1]), 13, 0, 7); g.strokeStyle = "#ffe14d"; g.lineWidth = 3; g.stroke(); }
    }
    g.restore();
  }
}
