# Consumption Balance V1 Simulation

- Samples per facility level: `100000`
- Base seed: `20260731`
- This is a local simulation for the consumption planning module only, not a full-economy simulation.
- It does not include capacity limits, lodging revenue, food shortages, breakdowns, ratings, or player decisions.
- The current probabilities and weights are still a first-pass balancing set.

## Dining

### Total Planned Action Rate

| Level | planned rate |
| --- | --- |
| Lv0 | 53.53% |
| Lv1 | 53.61% |
| Lv2 | 54.04% |

### Planned Action Rate By Spending Habit

| Level | spending_habit | planned rate |
| --- | --- | --- |
| Lv0 | low | 39.71% |
| Lv0 | mid | 55.31% |
| Lv0 | high | 69.80% |
| Lv1 | low | 40.27% |
| Lv1 | mid | 55.05% |
| Lv1 | high | 69.85% |
| Lv2 | low | 40.38% |
| Lv2 | mid | 55.57% |
| Lv2 | high | 70.62% |

### Tier Distribution By Facility Level

| Level | basic | standard | premium |
| --- | --- | --- | --- |
| Lv0 | 100.00% | 0.00% | 0.00% |
| Lv1 | 46.36% | 53.64% | 0.00% |
| Lv2 | 36.70% | 40.11% | 23.19% |

### Tier Distribution By Economic Level

| Level | economic_level | basic | standard | premium |
| --- | --- | --- | --- | --- |
| Lv0 | low | 100.00% | 0.00% | 0.00% |
| Lv0 | mid | 100.00% | 0.00% | 0.00% |
| Lv0 | high | 100.00% | 0.00% | 0.00% |
| Lv1 | low | 66.62% | 33.38% | 0.00% |
| Lv1 | mid | 36.98% | 63.02% | 0.00% |
| Lv1 | high | 39.60% | 60.40% | 0.00% |
| Lv2 | low | 60.03% | 29.92% | 10.04% |
| Lv2 | mid | 29.56% | 50.08% | 20.36% |
| Lv2 | high | 19.67% | 30.54% | 49.79% |

### Average Per Group

| Level | avg revenue | avg food use | avg satisfaction gain |
| --- | --- | --- | --- |
| Lv0 | 32.05 | 1.07 | 1.07 |
| Lv1 | 40.75 | 1.07 | 1.65 |
| Lv2 | 47.74 | 1.08 | 2.02 |

## Paid Entertainment

### Total Planned Action Rate

| Level | planned rate |
| --- | --- |
| Lv0 | 47.93% |
| Lv1 | 48.02% |
| Lv2 | 47.92% |

### Planned Action Rate By Spending Habit

| Level | spending_habit | planned rate |
| --- | --- | --- |
| Lv0 | low | 29.82% |
| Lv0 | mid | 50.24% |
| Lv0 | high | 69.28% |
| Lv1 | low | 30.32% |
| Lv1 | mid | 49.97% |
| Lv1 | high | 69.44% |
| Lv2 | low | 29.62% |
| Lv2 | mid | 50.17% |
| Lv2 | high | 69.64% |

### Tier Distribution By Facility Level

| Level | basic | standard | premium |
| --- | --- | --- | --- |
| Lv0 | 100.00% | 0.00% | 0.00% |
| Lv1 | 47.09% | 52.91% | 0.00% |
| Lv2 | 36.87% | 39.93% | 23.21% |

### Tier Distribution By Economic Level

| Level | economic_level | basic | standard | premium |
| --- | --- | --- | --- | --- |
| Lv0 | low | 100.00% | 0.00% | 0.00% |
| Lv0 | mid | 100.00% | 0.00% | 0.00% |
| Lv0 | high | 100.00% | 0.00% | 0.00% |
| Lv1 | low | 67.10% | 32.90% | 0.00% |
| Lv1 | mid | 37.72% | 62.28% | 0.00% |
| Lv1 | high | 40.46% | 59.54% | 0.00% |
| Lv2 | low | 60.06% | 29.63% | 10.30% |
| Lv2 | mid | 30.16% | 49.95% | 19.89% |
| Lv2 | high | 20.18% | 29.97% | 49.85% |

### Average Per Group

| Level | avg revenue | avg satisfaction gain |
| --- | --- | --- |
| Lv0 | 14.38 | 0.96 |
| Lv1 | 18.22 | 1.47 |
| Lv2 | 21.14 | 1.79 |

## Free Entertainment

| Level | raw hit rate | retained rate | dropped due to time / raw hits |
| --- | --- | --- | --- |
| Lv0 | 50.02% | 45.45% | 9.13% |
| Lv1 | 50.35% | 45.81% | 9.02% |
| Lv2 | 49.85% | 45.28% | 9.17% |

### By Arrival Turn

| Level | arrival_turn | raw hit rate | retained rate | drop rate |
| --- | --- | --- | --- | --- |
| Lv0 | Turn 2 | 50.00% | 50.00% | 0.00% |
| Lv0 | Turn 3 | 50.13% | 50.13% | 0.00% |
| Lv0 | Turn 4 | 49.92% | 36.21% | 27.45% |
| Lv1 | Turn 2 | 50.28% | 50.28% | 0.00% |
| Lv1 | Turn 3 | 50.28% | 50.28% | 0.00% |
| Lv1 | Turn 4 | 50.48% | 36.87% | 26.97% |
| Lv2 | Turn 2 | 50.01% | 50.01% | 0.00% |
| Lv2 | Turn 3 | 49.72% | 49.72% | 0.00% |
| Lv2 | Turn 4 | 49.83% | 36.12% | 27.51% |

## Combined

| Level | avg planned actions | all three actions | no action | avg potential revenue | dining / paid entertainment revenue |
| --- | --- | --- | --- | --- | --- |
| Lv0 | 1.47 | 9.03% | 12.95% | 46.43 | 2.23 |
| Lv1 | 1.47 | 9.14% | 12.55% | 58.96 | 2.24 |
| Lv2 | 1.47 | 8.97% | 12.73% | 68.88 | 2.26 |
