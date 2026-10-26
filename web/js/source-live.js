// Live source: Server-Sent Events. Replay source (S21) exposes the same interface.
export function connectLive(profile, h) {
  const es = new EventSource(`/stream?profile=${encodeURIComponent(profile)}`);
  const on = (type, fn) => es.addEventListener(type, (e) => fn && fn(JSON.parse(e.data)));
  on("meta", h.onMeta);
  on("frame", h.onFrame);
  on("event", h.onEvent);
  on("overlay", h.onOverlay);
  on("score", h.onScore);
  es.addEventListener("end", (e) => { h.onEnd && h.onEnd(JSON.parse(e.data)); es.close(); });
  es.onerror = () => { h.onError && h.onError(); };
  return es;
}
