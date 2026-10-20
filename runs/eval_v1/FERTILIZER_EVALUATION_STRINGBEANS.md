# Fertilizer Recommender Evaluation — String Beans Wiring (Eval SB)

## Context

The user wants to evaluate the VeggieCare fertilizer recommender as it would be used by **local farmers growing string beans**. "String beans" is **not** in the dataset's crop list (31 crops). The closest available crop is **Kidney Beans** — same species (*Phaseolus vulgaris*) — so it is used as the substitute. Per the user's requirement, **moisture is fixed at 50.0** (local farmers use NPK fertilizers like 14-14-14, not water-retaining ones, so moisture is not a meaningful input). Soil stays at the production default **"Loamy Soil"**. Nitrogen, Phosphorous and Potassium remain the actual per-row values from the dataset.

This is a re-run of the earlier production-wiring evaluation (Eval B), changing only the crop from `tomato` (which fell back to class 0) to `Kidney Beans` (a label the encoder actually knows).

## Dataset Stats

- Rows: **3100**
- Crops: **31** (each with 100 rows)
- Soils: **5** (Loamy Soil, Peaty Soil, Acidic Soil, Neutral Soil, Alkaline Soil)
- Fertilizer classes: **10**
- "Kidney Beans" rows: **100**
- "string beans" rows: **0** (not present)
- Unique Moisture values: 2960 (min 0.2274, max 0.9724) — moisture is effectively a near-unique per-row identifier
- Negative values: Potassium 71 rows, Phosphorous 78 rows, Nitrogen 0 rows

## Split

Stratified 80/20 split, `random_state=42`, `test_size=0.2`, stratified on Fertilizer → train **2480**, test **620** (identical split to Eval A/B).

## Eval SB — Features and Headline Metrics

Features for every test row: `[moisture=50.0, Nitrogen, Phosphorous, Potassium, soil_encoded("Loamy Soil")=2, crop_encoded("Kidney Beans")=8]`

- **Accuracy: 0.5726**
- **Macro-F1: 0.3627**
- Weighted-F1: 0.5387

### Per-class metrics

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Balanced NPK Fertilizer | 0.1255 | 1.0000 | 0.2230 | 31 |
| Compost | 0.2778 | 0.2000 | 0.2326 | 75 |
| DAP | 1.0000 | 1.0000 | 1.0000 | 211 |
| General Purpose Fertilizer | 0.0909 | 0.1667 | 0.1176 | 6 |
| Gypsum | 0.0000 | 0.0000 | 0.0000 | 11 |
| Lime | 1.0000 | 0.0278 | 0.0541 | 36 |
| Muriate of Potash | 1.0000 | 1.0000 | 1.0000 | 65 |
| Organic Fertilizer | 0.0000 | 0.0000 | 0.0000 | 19 |
| Urea | 1.0000 | 1.0000 | 1.0000 | 31 |
| Water Retaining Fertilizer | 0.0000 | 0.0000 | 0.0000 | 135 |
| **macro avg** | 0.4494 | 0.4394 | **0.3627** | 620 |
| **weighted avg** | — | — | **0.5387** | 620 |

## Confusion Matrix (Eval SB)

Rows = true class, columns = predicted class. Full labels:

- [0] Balanced NPK Fertilizer
- [1] Compost
- [2] DAP
- [3] General Purpose Fertilizer
- [4] Gypsum
- [5] Lime
- [6] Muriate of Potash
- [7] Organic Fertilizer
- [8] Urea
- [9] Water Retaining Fertilizer

| true \ pred | [0] | [1] | [2] | [3] | [4] | [5] | [6] | [7] | [8] | [9] |
|---|---|---|---|---|---|---|---|---|---|---|
| [0] Balanced NPK Fertilizer | 31 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| [1] Compost | 58 | 15 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| [2] DAP | 0 | 0 | 211 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| [3] General Purpose Fertilizer | 5 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| [4] Gypsum | 6 | 4 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| [5] Lime | 16 | 14 | 0 | 5 | 0 | 1 | 0 | 0 | 0 | 0 |
| [6] Muriate of Potash | 0 | 0 | 0 | 0 | 0 | 0 | 65 | 0 | 0 | 0 |
| [7] Organic Fertilizer | 13 | 5 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| [8] Urea | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 0 |
| [9] Water Retaining Fertilizer | 118 | 16 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

Saved as `runs/eval_v1/fert_eval_confusion_SB.csv` (527 bytes).

## Comparison to Previous Evaluations

| Evaluation | Wiring | Accuracy | Macro-F1 |
|---|---|---|---|
| Eval A | per-row actual features (model artifact quality) | 0.9694 | 0.9707 |
| Eval B | production: moisture=50, soil=Loamy, crop=tomato → fallback 0 | 0.5677 | 0.3787 |
| **Eval SB** | **moisture=50, soil=Loamy, crop=Kidney Beans** | **0.5726** | **0.3627** |
| Fresh RF | fresh RandomForest ceiling (n_estimators=200, clean split) | 0.8113 | 0.6738 |

## Literal "string beans" Fallback Check

- Passing `crop="string beans"` to `crop_encoder.transform()` raises **ValueError** (unseen label) → production wiring catches it and falls back to `crop_encoded=0`.
- `crop_encoded=0` corresponds to crop **"Adzuki Beans"** (index 0 in `crop_encoder.classes_`).
- ValueError -> fallback to crop_encoded=0.
- Consequence: if the system were deployed with the literal string "string beans", it would **silently** behave as if the crop were **Adzuki Beans** — no error shown to the user. This is the same failure mode as the `tomato` bug in Eval B.

## Honest Caveats

1. **Substitute crop**: Kidney Beans is a substitute for string beans (same species, *Phaseolus vulgaris*) but not identical. Recommendations are transferred, not measured.
2. **Eval A is still inflated** by train/test overlap (the model memorized per-row Moisture, a near-unique identifier). Do not read 0.9694 as real-world performance.
3. **Synthetic dataset**: the dataset is synthetic; real field data will differ.
4. **moisture=50 is a user requirement** based on local farming practice (NPK fertilizers, not water-retaining ones), not a measured value — it is held constant across all rows in this eval.
5. **Only the test split is scored**; the Kidney Beans training rows (80 of 100) remain in the training set, so the model has seen this crop during training — but with the *actual* crop feature, not the hardcoded one used here.

## Key Takeaways

1. **Eval SB accuracy is 0.5726 (macro-F1 0.3627)** — essentially the same as the tomato-fallback Eval B (0.5677). Replacing the class-0 fallback with the real "Kidney Beans" label recovers almost nothing (+0.0048), because the crop feature is hardcoded to a single value for every row and can no longer discriminate between rows.
2. **The model collapses to NPK-only rules**: with crop and soil constant, predictions are driven almost entirely by N/P/K — DAP, Muriate of Potash and Urea are predicted perfectly (pure-NPK classes), while classes that need crop/soil context (Compost, Gypsum, Lime, Organic, Water Retaining) collapse into Balanced NPK.
3. **The unseen-label fallback is silent**: "string beans" → ValueError → crop_encoded=0 → the model pretends the crop is **Adzuki Beans**. Any deployment must validate crop input against the encoder and surface an error, not fall back.
4. **Hardcoding moisture=50 costs accuracy** vs per-row moisture (Eval A 0.9694), but is required by the user's local-farming context.
5. **Treat all absolute numbers with caution**: synthetic data + train/test overlap mean the headline figures are indicative, not field-validated.
