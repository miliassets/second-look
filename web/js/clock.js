// One match clock. Live mode follows frame times; replay mode (S21) runs off recorded times.
export class Clock {
  constructor() { this.ms = 0; this.label = "0:00"; this.speedLabel = ""; }
  set(ms, label) { if (ms >= this.ms) { this.ms = ms; if (label) this.label = label; } }
}

// Global match_ms -> display clock (67:12 / 45+2:10) using the period table from meta.
export function formatMs(ms, periods) {
  let acc = 0, idx = 0;
  for (let i = 0; i < periods.length; i++) { if (ms >= acc) idx = i; acc += periods[i].length_ms; }
  let off = 0; for (let i = 0; i < idx; i++) off += periods[i].length_ms;
  const clockMs = ms - off, period = periods[idx].period;
  const base = { 1: 0, 2: 45, 3: 90, 4: 105 }[period], nominal = period <= 2 ? 45 : 15;
  const mins = Math.floor(clockMs / 60000), secs = Math.floor((clockMs % 60000) / 1000);
  const pad = String(secs).padStart(2, "0");
  return mins >= nominal ? `${base + nominal}+${mins - nominal}:${pad}` : `${base + mins}:${pad}`;
}
