---
name: sklearn-to-js
description: Export a scikit-learn decision tree to zero-dependency pure JavaScript for on-device inference in openvela quick apps. Use when the user has a trained tree-based model and needs to run it on a watch/wearable with no Python runtime, or when adding on-device ML to a .ux quick app.
license: Apache-2.0
---

# sklearn-to-js

Export a scikit-learn `DecisionTreeClassifier` into a single zero-dependency JavaScript module that runs directly on openvela quick apps (`.ux`) — no Python, NumPy or model server at runtime.

## When to use

- Adding on-device ML inference to an openvela quick app / watch app
- The user has a trained tree model and needs it as portable JS
- Offline-first, low-latency inference (no round-trip to a model server)

## Core workflow

1. Put labelled features in a CSV (columns = features, one `label` column)
2. Run `scripts/export_tree.py` — it trains a compact tree and emits an `if/else` chain
3. The generated `predict(features)` runs anywhere JavaScript runs

```bash
python scripts/export_tree.py \
  --csv datasets/features.csv \
  --output src/common/generated-model.js \
  --label-column label \
  --max-depth 4
```

## Key constraints (read before integrating)

- **Feature order is a contract.** The CSV column order, the training array, and the JS `predict(features)` input MUST match. The generated file documents the order in a comment — keep it in sync.
- **Keep the regression check.** The script compares `model.predict()` (Python) against expected labels on anchor vectors before writing JS; it catches export bugs. Do not delete it.
- **Prefer a compact tree.** `max_depth` 3–5 keeps the JS tiny and interpretable. A deeper tree grows the bundle with little accuracy gain — better features beat a deeper tree.
- **Do not over-claim accuracy from synthetic data.** Label the data source (simulator / public dataset / real device) honestly in any report.

## Feature design

For guidance on designing input features (composition vs. single thresholds, and the fall-detection example this skill was extracted from), see [references/feature-engineering.md](references/feature-engineering.md).
