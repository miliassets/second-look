# Realism report (100 random seeds)

97 of 100 seeds pass every range (ranges `UNVERIFIED`; typical whole-match totals, both teams).

| Measure | Range | Mean | Min | Max | Seeds outside |
| :- | :- | -: | -: | -: | -: |
| passes | 800-1100 | 930.0 | 876.0 | 994.0 | 0 |
| pass_completion_pct | 75-88 | 85.5 | 82.7 | 89.4 | 3 |
| shots | 18-32 | 24.0 | 22.0 | 26.0 | 0 |
| goals | 0-7 | 2.4 | 0.0 | 6.0 | 0 |
| corners | 6-14 | 8.8 | 7.0 | 12.0 | 0 |
| fouls | 16-28 | 23.2 | 21.0 | 25.0 | 0 |
| yellow_cards | 1-6 | 3.2 | 1.0 | 5.0 | 0 |
| possession_min_pct | 35-50 | 46.3 | 39.9 | 49.9 | 0 |
| possession_max_pct | 50-65 | 53.7 | 50.1 | 60.1 | 0 |
| shot_speed_min_kmh | 50-200 | 55.8 | 52.0 | 70.5 | 0 |
| shot_speed_max_kmh | 0-120 | 104.8 | 89.1 | 118.0 | 0 |

Goals per match: mean 2.35 over 100 seeds (target about 2.7).

## Failing seeds

- seed 51: pass_completion_pct=89.4 outside 75-88
- seed 66: pass_completion_pct=88.0 outside 75-88
- seed 79: pass_completion_pct=89.1 outside 75-88

## Demo match (seed 20261024)

| Measure | Value |
| :- | -: |
| passes | 933.0 |
| pass_completion_pct | 84.9 |
| shots | 24.0 |
| goals | 2.0 |
| corners | 10.0 |
| fouls | 24.0 |
| yellow_cards | 1.0 |
| shot_speed_min_kmh | 62.5 |
| shot_speed_max_kmh | 112.0 |
| possession_min_pct | 49.8 |
| possession_max_pct | 50.2 |
| distance_km | 11.9 |

All ranges pass.
