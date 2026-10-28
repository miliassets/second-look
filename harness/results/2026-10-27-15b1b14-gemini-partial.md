# Single-agent comparison (2026-10-27, 15b1b14)

> **PARTIAL RUN (5 of 30 sequences).** Not a complete result and not to be reported as one.

Provider `gemini`, model `gemini`, 5 held-out sequences (10 clean, 10 misleading possession, 5 late corrections, 5 injected timeouts). 95 % bootstrap intervals over sequences in brackets.

| Metric | multi | single | no_editor |
| :- | -: | -: | -: |
| Unsupported claims / 100 proposed (lower is better) | 5.4 [0.0, 11.8] | 5.0 [0.0, 12.5] | 0.0 [0.0, 0.0] |
| Published accuracy % (higher) | 20.0 [0.0, 60.0] | 0.0 [0.0, 0.0] | 40.0 [0.0, 80.0] |
| Fallback rate % (lower) | 0.0 [0.0, 0.0] | 60.0 [20.0, 100.0] | 20.0 [0.0, 60.0] |
| Corrections in both views <= 5 s % (higher) | 100.0 [100.0, 100.0] | 100.0 [100.0, 100.0] | 100.0 [100.0, 100.0] |
| p95 latency ms (lower) | 2715.0 [2212.0, 2715.0] | 12429.0 [9194.0, 12429.0] | 10510.0 [9468.0, 10510.0] |
| Survival under fault % (higher) | n/a | n/a | n/a |
| Tokens per completed moment (lower) | 63430.4 [55692.2, 72680.0] | 71723.0 [25821.2, 175413.0] | 70561.2 [50506.2, 134208.0] |

Publish what comes out, including losses. If `multi` is not better on any metric, tell the Champion before the video is scripted.
