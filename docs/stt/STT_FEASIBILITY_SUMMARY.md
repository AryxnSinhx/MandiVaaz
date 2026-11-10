# STT feasibility summary (Prisma v3.0)

Clips scored: **40** from **5** speaker label(s) (1, 2, 3, 4, 5). Counts are small: treat differences under ~10 percentage points as noise.

## Slot accuracy per config (hits / clips)

| Config | Crop | Mandi | **Both** | Empty/err | Latency p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| pa_verbatim | 36/40 | 38/40 | **34/40** | 0 | 1.11s | 2.71s | 3.31s |
| pa_codemix | 38/40 | 37/40 | **35/40** | 0 | 1.32s | 2.05s | 2.64s |
| paen_codemix | 38/40 | 38/40 | **36/40** | 0 | 1.30s | 2.57s | 2.74s |
| pahien_codemix | 38/40 | 38/40 | **36/40** | 0 | 1.24s | 2.50s | 4.46s |
| auto_codemix | 38/40 | 38/40 | **36/40** | 0 | 1.23s | 2.71s | 3.09s |
| translit | 35/40 | 37/40 | **33/40** | 0 | 1.43s | 2.58s | 2.86s |
| paen_codemix_ctx | 35/40 | 39/40 | **34/40** | 0 | 1.39s | 3.38s | 4.37s |

Latency = client-measured round trip (includes upload), first call per config discarded.

## By question group (Both hits / clips)

A = pure Punjabi, B = Punglish, C = Hindi-mixed

| Config | A | B | C |
|---|---|---|---|
| pa_verbatim | 13/14 | 10/14 | 11/12 |
| pa_codemix | 13/14 | 12/14 | 10/12 |
| paen_codemix | 13/14 | 12/14 | 11/12 |
| pahien_codemix | 13/14 | 12/14 | 11/12 |
| auto_codemix | 13/14 | 12/14 | 11/12 |
| translit | 10/14 | 13/14 | 10/12 |
| paen_codemix_ctx | 13/14 | 14/14 | 7/12 |

## By speaker (Both hits / clips)

| Config | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| pa_verbatim | 8/10 | 9/10 | 5/6 | 5/6 | 7/8 |
| pa_codemix | 9/10 | 9/10 | 5/6 | 5/6 | 7/8 |
| paen_codemix | 9/10 | 9/10 | 5/6 | 6/6 | 7/8 |
| pahien_codemix | 9/10 | 9/10 | 5/6 | 6/6 | 7/8 |
| auto_codemix | 9/10 | 9/10 | 5/6 | 6/6 | 7/8 |
| translit | 9/10 | 10/10 | 4/6 | 5/6 | 5/8 |
| paen_codemix_ctx | 10/10 | 8/10 | 4/6 | 5/6 | 7/8 |

## Upper bounds if we ran several configs per clip

Assumes a perfect chooser (we would not know which is right), so reality is lower.

- best two (paen_codemix + pahien_codemix): 36/40
- any config: 38/40

WER on group A, non-translit configs: mean 0.06 over 84 outputs. Diagnostic only; your references must match what was actually said.