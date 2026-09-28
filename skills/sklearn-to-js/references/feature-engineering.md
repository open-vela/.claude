# Feature Engineering for On-Device Trees

A tree is only as good as its features. This reference covers the principles
behind robust on-device feature design, with the fall-detection model this
skill was extracted from as a worked example.

## Core principle: a single threshold is not enough

A single high reading is rarely a reliable signal. Real-world classes overlap,
and a lone threshold produces false positives. Design features so that the
decision requires a **combination** of independent conditions.

For fall detection, a high acceleration peak alone is insufficient — running,
jumping and shaking also produce peaks. A fall additionally shows:

1. a free-fall phase (low magnitude *before* impact)
2. a sharp impact (high peak)
3. an orientation change
4. a quiet tail (the body comes to rest after impact)

Only when several of these co-occur should the tree vote `fall`.

## Worked example: 7-feature fall detector

The `sklearn-to-js` skill originated from a wearable fall/immobility detector.
Its window features (computed over a sliding IMU window, 80 ms sampling):

| Feature | Meaning | Why it matters |
|---|---|---|
| `peakG` | max combined acceleration | catches the impact |
| `minG` | min combined acceleration | catches the free-fall drop |
| `variance` | full-window variance | separates sustained motion from a single jolt |
| `postVariance` | variance of the tail | detects the "quiet after impact" |
| `orientationChange` | first-to-last Z-axis change | catches a flip/fall in posture |
| `lowMotionRatio` | share of low-delta samples | quantifies coming to rest |
| `durationMs` | window length | separates transient from sustained |

Combined magnitude is `sqrt(x^2 + y^2 + z^2)`; variance is computed over that
series. The tail is the last ~10 samples.

### Class design

- `normal` — everyday motion, including **hard negatives**: high peak but no
  free-fall/quiet-tail combination (run, shake).
- `fall` — free-fall + impact + orientation change + quiet tail together.
- `immobility` — sustained low activity *without* an impact (fall without the
  peak).

Deliberately include hard negatives in training data (e.g. running, violent
shaking). Without them the tree will happily misclassify a shake as a fall.

## Keep the model small

`max_depth` 3–5 is the sweet spot for on-device trees:

- tiny, readable `if/else` bundle (no serialization runtime)
- interpretable thresholds you can sanity-check
- low latency and near-zero memory

If accuracy is insufficient, improve the **features** before deepening the
tree — add a feature that disambiguates the confusing classes, or include more
hard negatives in the data.

## Data ethics

- Never collect fall data through dangerous real-person falls.
- Distinguish simulator validation, public-dataset evaluation and real-device
  results in any report. Do not present synthetic-data accuracy as real-world
  accuracy.
- The CSV used here is a *feature-level* engineering sample, not a clinical
  fall dataset — state this when it applies to your project.
