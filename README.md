# attribution-pipeline

**Multi-touch attribution on GA4-style event data — the marketing measurement craft of a GA4 / web-analytics day job — as one transparent, runnable artifact.**

Every marketing team argues about the same question: which channels actually earned the revenue? The answer depends entirely on the attribution model, and in most companies that model is a dropdown in a tool nobody can inspect. This project rebuilds the whole chain as code a reviewer can run, test, and argue with: a deterministic synthetic GA4-style event stream for a fictional smart-home store (**Helios Home**), journey stitching with a documented 30-day lookback and dedupe rule, five fractional attribution models whose credit is conserved to the cent, and a channel readout — credited revenue, spend, ROAS, CPA — that names exactly where the models disagree. No black box, no ML library: the disagreement *is* the deliverable, because choosing a model is a business decision, not a default.

> **Synthetic data only.** Every event, conversion, and spend row is fabricated by a seeded generator. No real analytics, customer, or revenue data is used anywhere in this project.

Stdlib-only Python. Zero network, zero API keys, zero external dependencies.

## Quickstart

```bash
# 1. Generate the synthetic Helios Home dataset (1,500 users, seed 42)
#    -> dataset/ holding events / conversions / spend as .jsonl
python3 cli.py generate --count 1500 --seed 42 --out-dir dataset

# 2. Stitch events + conversions into user journeys
python3 cli.py journeys --input dataset --limit 5

# 3. Run one attribution model (per-conversion fractional credit as JSON)
python3 cli.py attribute --input dataset --model position_based

# 4. Channel readout across all five models, with spend / ROAS / CPA
python3 cli.py report --input dataset

# 5. CI gate: exit 0 = pass, 1 = credit-error / conversion-floor breach,
#    2 = data-quality / IO error
python3 cli.py check --input dataset --max-credit-error 0.000001 --min-conversions 100

# 6. One-command end-to-end demo (generate -> stitch -> attribute -> report -> serve)
python3 demo.py

# 7. Tiny localhost API: POST /attribute, GET /report, GET /health on 127.0.0.1
python3 cli.py serve --port 8765 --input dataset
```

## The seeded dataset at a glance (seed 42, 1,500 users)

4,257 touchpoint events stitch into 4,087 journey touches (170 consecutive duplicate touches collapsed by the dedupe rule); 235 users convert for **$45,160.00** against **$32,517.84** of channel spend. Under the linear model:

| Channel | Touches | Assisted conv. | Spend | Credited revenue | ROAS |
|---|---|---|---|---|---|
| organic_search | 732 | 105 | $1,767.17 | $7,595.43 | 4.30 |
| paid_search | 659 | 107 | $12,577.90 | $8,396.65 | 0.67 |
| paid_social | 570 | 76 | $9,552.74 | $5,209.49 | 0.55 |
| organic_social | 432 | 67 | $1,018.98 | $4,516.63 | 4.43 |
| display | 480 | 70 | $5,516.68 | $5,479.99 | 0.99 |
| email | 426 | 77 | $1,338.01 | $5,562.34 | 4.16 |
| referral | 310 | 47 | $746.36 | $3,236.33 | 4.34 |
| direct | 478 | 84 | $0.00 | $5,163.13 | — (zero spend) |

**Where the models fight:** the channel the five models disagree about most is **direct** — first-touch credits it $7,256.00, last-touch only $3,067.00, a **$4,189.00 spread** on the same data. Paid search, meanwhile, earns the most credited revenue under linear ($8,396.65) yet returns ROAS 0.67 against its $12,577.90 spend — the classic "biggest channel, worst efficiency" readout that starts the budget argument. These numbers are pinned by golden evals E2/E4/E8, so any change to the generator, the stitcher, or a model shows up as an eval failure, not a surprise.

## How attribution works

All five models distribute exactly 1.0 of credit per conversion across its journey's touches (repeated touches on one channel sum). They differ only in *where* the credit lands:

| Model | Rule | What it flatters |
|---|---|---|
| `first_touch` | 100% to the first touch | Prospecting — "what started it" |
| `last_touch` | 100% to the last touch | Closers — the GA4-default-era view |
| `linear` | Equal split across every touch | Nobody in particular; the neutral baseline |
| `position_based` | U-shape: 40% first, 40% last, middle touches split 20% (1 touch = 100%; 2 touches = 50/50) | Openers and closers over assists |
| `time_decay` | Weight `2^(−age_days/7)` per touch (7-day half-life), normalised | Whatever happened most recently |

**Stitching rules** (each pinned by tests and eval E13): touches are grouped per user and sorted by `(timestamp, event_id)`; a conversion's journey is every touch in the 30-day lookback ending at the conversion, both bounds inclusive; a touch after a conversion belongs to the *next* journey, so no touch is ever double-counted; **consecutive touches with the same `(channel, campaign)` collapse into one** (earliest timestamp kept) — GA4 exports fire duplicates on page reloads and double-tagged pages, and counting them would hand free credit to whichever channel glitched. A conversion whose lookback window holds no touches books its revenue under an `unattributed` bucket instead of inventing a channel.

**The generator.** A seeded PRNG (`random.Random(seed)`) draws each user's touches (channel mix, campaign, device, sessions split at 30-minute gaps), then a latent propensity — a 0.05 base rate plus a per-touch lift by channel (paid_search +0.060, email +0.055, organic_search +0.040, down to display +0.012) — decides conversion. The lifts are the ground truth the models are unknowingly arguing about: on the seeded book, users touched by paid search convert at 19.5% vs 13.5% for everyone else (pinned by a unit test). Spend is a deterministic per-channel-per-day table (240 rows; `direct` spends $0 by definition). Same seed, same dataset, byte-for-byte (eval E1 pins the events JSONL md5).

## Architecture

| Module | Job |
|---|---|
| `generator.py` | Deterministic synthetic Helios Home dataset: touchpoint events, conversions (latent per-channel propensity), daily channel spend; JSONL / CSV serialisers + loaders with explicit numeric coercion |
| `stitching.py` | Journey building: per-user ordering, 30-day lookback, conversion boundaries (a touch belongs to exactly one journey), consecutive `(channel, campaign)` dedupe, trailing-touch open journeys |
| `models.py` | Five fractional attribution models as pure functions; per-conversion credits sum to 1.0 within 1e-9; repeated channels aggregated |
| `report.py` | Channel readout per model (credited conversions / revenue, spend join, ROAS, CPA with zero-handling) + cross-model comparison naming the max-disagreement channel |
| `validation.py` | Data-quality gate: required fields, channel / device vocabularies, timestamp parsing, revenue / spend ≥ 0, duplicate ids, conversion-before-first-touch — reports issues, never raises on bad data |
| `cli.py` | `generate` / `journeys` / `attribute` / `report` / `check` (CI exit 0/1/2) / `demo` / `serve` (stdlib `http.server` on 127.0.0.1: `POST /attribute`, `GET /report`, `GET /health`); datasets are directories of JSONL or CSV tables |
| `demo.py` | End-to-end narrative with asserts: determinism, credit conservation for all 5 models, CLI exit codes, live localhost serve round-trip where the served report matches the local report exactly |
| `fixtures/` | 60-user sample dataset (seed 42) in both JSONL and CSV: 165 events, 11 conversions, $3,364.00 revenue, 240 spend rows |

## Evals

`python3 evals/run_evals.py` → **13/13 golden evals**, report committed at `evals/eval_report.json` (md5 `b99523f6ad5751397a7c7c7a45e6a13a`, byte-identical across 3 verified runs):

- **E1** generator determinism — seed 42 events JSONL bytes pinned by md5
- **E2** seeded counts pinned (4,257 events, 235 conversions, $45,160.00 revenue, 4,087 stitched touches)
- **E3** credit sums to 1.0 and credited revenue equals actual revenue, all 5 models
- **E4** first vs last touch pinned on `direct` ($7,256.00 vs $3,067.00; max-disagreement spread $4,189.00)
- **E5** all 46 single-touch conversions get full credit in every model
- **E6** position-based weights pinned on 1/2/3/5-touch journeys
- **E7** time-decay: 7-day half-life exact (2/3 vs 1/3), recent touch beats older touch
- **E8** spend/ROAS readout pinned (paid_search: $12,577.90 spend, $8,396.65 linear revenue, ROAS 0.6676)
- **E9** validation flags planted defects (bad channel, negative revenue, duplicate event id, negative spend); generated dataset validates clean
- **E10** CLI `check` exit codes 0 / 1 / 2
- **E11** serve round-trip matches local attribution and report exactly
- **E12** bundled fixture pinned (165 events, 11 conversions, $3,364.00, validates clean)
- **E13** stitching rules pinned (30-day lookback boundary, post-conversion touch → next journey, dedupe collapse)

Unit tests: `python3 -m unittest discover -s tests` → **119 hermetic tests**, including a no-network enforcement test (sockets stubbed), generator determinism and latent-signal checks, stitching lookback/boundary/dedupe rules, every model's math (position-based shapes, time-decay half-life to 9 decimal places), report math and zero-handling, data-quality validation, CLI exit codes, and serve round-trips.

## Development loop — what the checks validated

One test failed on the first full run, and it was the test that was wrong, not the code:

1. **The dedupe rule applies to *every* journey, converting or not.** The first draft of the non-converting-journey test built two touches with the same `(channel, campaign)` and expected both to survive stitching; the stitcher collapsed them to one, exactly as the documented rule requires. The fix was the fixture (a genuinely different second touch), and the episode pinned an easy-to-miss consequence: open journeys get the same dedupe treatment as converting ones — otherwise non-converting touch counts would be silently inflated relative to converting ones and every touchpoint total in the report would be apples-to-oranges.

Two further design decisions the suite pins before they can regress:

2. **Revenue conservation survives the `unattributed` bucket.** A conversion with an empty lookback window has no channel to credit; rather than dropping its revenue (breaking "credited == actual" for every model) or inventing a touch, the report books it under `unattributed` and the check gate's credit-error computation includes it. On the seeded book the bucket is empty (E2 pins 0 unattributed) — but the hand-built edge case is tested so the invariant holds on messier data than the generator produces.
3. **CSV input needs the same explicit coercion layer as every sibling build.** CSV parses revenue and spend as strings; `load_conversions` / `load_spend` coerce them to floats, pinned by a generate → CSV → report round-trip test that must reproduce the JSONL numbers exactly.

## Scope and honest limits

- **These are rule-based models, not causal measurement.** First/last/linear/position/time-decay redistribute credit for conversions that already happened; none of them estimates *incrementality* — what would not have happened without the spend. That requires holdouts, geo experiments, or a fitted model (e.g. Markov chains / Shapley values over observed paths, or a media-mix model). This project is the deterministic bookkeeping layer those methods sit on top of, and it says so.
- **The generator's propensity is by construction.** Paid search and email "work" in this data because the generator's lift table says so — the honest claim is that the pipeline recovers and argues about known ground truth reproducibly, not that any channel finding transfers to a real store.
- **The observation window is right-censored.** Journeys start in the first 22 days so conversions land inside the 30-day window; real GA4 exports have the same censoring shape at both edges, and a production pipeline would model it explicitly.
- **One conversion per user in the generator; many in the stitcher.** The generator keeps its population simple, but `stitching.py` handles multi-conversion users (and pins the boundary rule by test) because real GA4 data is full of them.
- **The API is a demo surface.** It binds 127.0.0.1 only, holds its dataset in memory, and resets on restart.

## Why this project

It takes the author's day-job craft — marketing measurement: GA4 event data, attribution modelling, spend-vs-revenue readouts — and rebuilds it as an analytics-engineering artifact: a deterministic data generator with a data-quality gate, five inspectable models with pinned golden evals, a CI check command, and a small API. The same discipline a forward-deployed engineer brings to a customer problem: make the logic inspectable, make the outputs reproducible, and make the failure modes exit codes.
