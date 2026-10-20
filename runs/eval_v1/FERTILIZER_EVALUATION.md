# Fertilizer Recommender — Model Evaluation Report

**Date:** 2026-10-19
**Artifact under test:** `models/fertilizer_rf.onnx` (+ `soil_encoder.joblib`, `crop_encoder.joblib`, `fert_encoder.joblib`)
**Dataset:** `fertilizer-data/fertilizer_recommendation_dataset.csv`
**Production wrapper:** `recommendations/onnx_recommender.py`

---

## 1. Context

The VeggieCare fertilizer recommender is a random-forest classifier exported to ONNX. It takes 6
features — `[Moisture, Nitrogen, Phosphorous, Potassium, soil_encoded, crop_encoded]` — and predicts
one of 10 fertilizer classes. This evaluation scores the shipped model against its labeled dataset
and, separately, scores the **production feature-wiring** to quantify the gap between "model quality"
and "what the deployed code actually computes."

Three evaluations were run on a single stratified split:

| Eval | Purpose | Features used |
|------|---------|---------------|
| **A** | Model artifact quality | Per-row **actual** Moisture/Soil/Crop |
| **B** | Production wiring (as-deployed) | Hardcoded `moisture=50.0`, `soil="Loamy Soil"`, `crop="tomato"` |
| **Ref** | Fresh-model ceiling | Per-row actual; new `RandomForestClassifier` |

### Environment

| Component | Version |
|-----------|---------|
| Python | 3.14.7 |
| onnxruntime | 1.31.0 |
| scikit-learn | 1.9.1 |
| joblib | 1.6.0 |
| pandas | 3.0.6 |
| numpy | 2.5.2 |
| Platform | Windows-11 (win32) |

### Dataset stats

- **Total rows:** 3100
- **Columns:** `Moisture, Nitrogen, Phosphorous, Potassium, Soil, Crop, Fertilizer`
- **Unique crops:** 31
- **Unique soils:** 5
- **Unique fertilizers (classes):** 10

**Per-fertilizer class counts:**

| Fertilizer | Count |
|------------|------:|
| DAP | 1054 |
| Water Retaining Fertilizer | 675 |
| Compost | 375 |
| Muriate of Potash | 326 |
| Lime | 181 |
| Balanced NPK Fertilizer | 157 |
| Urea | 154 |
| Organic Fertilizer | 95 |
| Gypsum | 52 |
| General Purpose Fertilizer | 31 |

**Per-soil counts:** Acidic 1519 · Peaty 537 · Neutral 510 · Loamy 489 · Alkaline 45

**Per-crop counts:** 31 crops × 100 rows each (perfectly balanced).

### Dataset quirks (synthetic-data signals)

- **Moisture:** 2960 unique values across 3100 rows (only ~140 repeats) — range 0.2274–0.9724.
- **Negative Potassium:** 71 rows.
- **Negative Phosphorous:** 78 rows.
- **Negative Nitrogen:** 0 rows.
- The combination of perfectly balanced crops (100 each), repeated moisture values, and
  physically-impossible negative nutrient values strongly indicates **synthetic / generated data**.

### Split

- Stratified 80/20 on `Fertilizer`, `random_state=42`, `test_size=0.2`.
- **Train:** 2480 rows · **Test:** 620 rows.
- All 10 classes present in both splits (stratified).

### Model / encoder introspection

- ONNX input: `float_input` `[None, 6]` `tensor(float)`.
- ONNX outputs: `output_label` `[None]` `int64`, `output_probability` `seq(map(int64,tensor(float)))`.
- `fert_encoder.classes_` (10): Balanced NPK Fertilizer, Compost, DAP, General Purpose Fertilizer,
  Gypsum, Lime, Muriate of Potash, Organic Fertilizer, Urea, Water Retaining Fertilizer.
- `soil_encoder.classes_` (5): Acidic, Alkaline, Loamy, Neutral, Peaty.
- `crop_encoder.classes_` (31): all dataset crops.

> **⚠ Production label mismatch:** the production wrapper hardcodes `crop="tomato"`, but
> **`tomato` is NOT in `crop_encoder.classes_`** (the dataset has 31 crops, none named tomato).
> The wrapper's `try/except ValueError` silently falls back to `crop_encoded = 0`. Eval B
> reproduces this exact behavior.

---

## 2. Eval A — Model artifact quality (per-row actual features)

*Headline "model accuracy" result. Features built from each row's real Moisture/Soil/Crop.*

**Accuracy = 0.9694 · macro-F1 = 0.9707 · weighted-F1 = 0.9687**

| Fertilizer | Precision | Recall | F1 | Support |
|------------|----------:|-------:|----:|--------:|
| Balanced NPK Fertilizer | 0.94 | 1.00 | 0.97 | 31 |
| Compost | 1.00 | 0.80 | 0.89 | 75 |
| DAP | 1.00 | 1.00 | 1.00 | 211 |
| General Purpose Fertilizer | 1.00 | 1.00 | 1.00 | 6 |
| Gypsum | 1.00 | 0.91 | 0.95 | 11 |
| Lime | 1.00 | 0.92 | 0.96 | 36 |
| Muriate of Potash | 1.00 | 1.00 | 1.00 | 65 |
| Organic Fertilizer | 1.00 | 1.00 | 1.00 | 19 |
| Urea | 1.00 | 1.00 | 1.00 | 31 |
| Water Retaining Fertilizer | 0.89 | 1.00 | 0.94 | 135 |
| **macro avg** | **0.98** | **0.96** | **0.97** | 620 |
| **weighted avg** | **0.97** | **0.97** | **0.97** | 620 |

**Accuracy: 0.9694**

---

## 3. Eval B — Production wiring (as-deployed)

*Same test rows, but features built exactly as `onnx_recommender.py` does: `moisture=50.0`,
`soil="Loamy Soil"`, `crop="tomato"` (→ `crop_encoded=0` via the ValueError fallback) for every row.*

**Accuracy = 0.5677 · macro-F1 = 0.3787 · weighted-F1 = 0.5353**

| Fertilizer | Precision | Recall | F1 | Support |
|------------|----------:|-------:|----:|--------:|
| Balanced NPK Fertilizer | 0.13 | 1.00 | 0.23 | 31 |
| Compost | 0.31 | 0.13 | 0.19 | 75 |
| DAP | 1.00 | 1.00 | 1.00 | 211 |
| General Purpose Fertilizer | 0.05 | 0.33 | 0.08 | 6 |
| Gypsum | 0.67 | 0.18 | 0.29 | 11 |
| Lime | 0.00 | 0.00 | 0.00 | 36 |
| Muriate of Potash | 1.00 | 1.00 | 1.00 | 65 |
| Organic Fertilizer | 0.00 | 0.00 | 0.00 | 19 |
| Urea | 1.00 | 1.00 | 1.00 | 31 |
| Water Retaining Fertilizer | 0.00 | 0.00 | 0.00 | 135 |
| **macro avg** | **0.42** | **0.46** | **0.38** | 620 |
| **weighted avg** | **0.55** | **0.57** | **0.54** | 620 |

**Accuracy: 0.5677**

> The production wiring discards the row's real Soil and Crop and pins Moisture to 50.0. Because the
> model leans heavily on Soil/Crop, collapsing them to constants destroys discriminative power for
> 7 of 10 classes (Lime, Organic, Water Retaining drop to F1 = 0). Only the NPK-driven classes
> (DAP, Muriate of Potash, Urea) survive.

---

## 4. Reference — Fresh RandomForest ceiling

*A clean `RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)` trained on the train
split with correct per-row features, evaluated on the test split. Estimates the generalization
ceiling of this model class on this data.*

**Accuracy = 0.8113 · macro-F1 = 0.6738**

| Fertilizer | Precision | Recall | F1 | Support |
|------------|----------:|-------:|----:|--------:|
| Balanced NPK Fertilizer | 0.64 | 0.87 | 0.74 | 31 |
| Compost | 0.39 | 0.19 | 0.25 | 75 |
| DAP | 1.00 | 1.00 | 1.00 | 211 |
| General Purpose Fertilizer | 0.40 | 0.33 | 0.36 | 6 |
| Gypsum | 1.00 | 0.18 | 0.31 | 11 |
| Lime | 0.64 | 0.44 | 0.52 | 36 |
| Muriate of Potash | 1.00 | 1.00 | 1.00 | 65 |
| Organic Fertilizer | 0.69 | 0.95 | 0.80 | 19 |
| Urea | 1.00 | 1.00 | 1.00 | 31 |
| Water Retaining Fertilizer | 0.66 | 0.87 | 0.75 | 135 |
| **macro avg** | **0.74** | **0.68** | **0.67** | 620 |
| **weighted avg** | **0.80** | **0.81** | **0.79** | 620 |

> The fresh RF (0.8113) is **well below** the shipped model's Eval A (0.9694). This gap is the
> signature of **train/test overlap** — the shipped model was almost certainly trained on this same
> dataset, so Eval A is optimistic (see Caveats).

---

## 5. Confusion matrices

Full matrices are saved as CSV (rows = true, columns = predicted):

- **Eval A:** `runs/eval_v1/fert_eval_confusion_A.csv`
- **Eval B:** `runs/eval_v1/fert_eval_confusion_B.csv`

Class order (both axes): `Balanced NPK Fertilizer, Compost, DAP, General Purpose Fertilizer, Gypsum, Lime, Muriate of Potash, Organic Fertilizer, Urea, Water Retaining Fertilizer`

### Eval A — confusion matrix

| true \ predicted | BalNPK | Compost | DAP | GenPurp | Gypsum | Lime | MoP | Organic | Urea | WaterRet |
|------------------|-------:|--------:|----:|--------:|-------:|-----:|----:|--------:|-----:|---------:|
| Balanced NPK Fertilizer | **31** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Compost | 2 | **60** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 13 |
| DAP | 0 | 0 | **211** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| General Purpose Fertilizer | 0 | 0 | 0 | **6** | 0 | 0 | 0 | 0 | 0 | 0 |
| Gypsum | 0 | 0 | 0 | 0 | **10** | 0 | 0 | 0 | 0 | 1 |
| Lime | 0 | 0 | 0 | 0 | 0 | **33** | 0 | 0 | 0 | 3 |
| Muriate of Potash | 0 | 0 | 0 | 0 | 0 | 0 | **65** | 0 | 0 | 0 |
| Organic Fertilizer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **19** | 0 | 0 |
| Urea | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **31** | 0 |
| Water Retaining Fertilizer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **135** |

*Errors: Compost→BalNPK (2), Compost→WaterRet (13), Gypsum→WaterRet (1), Lime→WaterRet (3).*

### Eval B — confusion matrix

| true \ predicted | BalNPK | Compost | DAP | GenPurp | Gypsum | Lime | MoP | Organic | Urea | WaterRet |
|------------------|-------:|--------:|----:|--------:|-------:|-----:|----:|--------:|-----:|---------:|
| Balanced NPK Fertilizer | **31** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Compost | 59 | **10** | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| DAP | 0 | 0 | **211** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| General Purpose Fertilizer | 4 | 0 | 0 | **2** | 0 | 0 | 0 | 0 | 0 | 0 |
| Gypsum | 5 | 2 | 0 | 2 | **2** | 0 | 0 | 0 | 0 | 0 |
| Lime | 11 | 4 | 0 | 21 | 0 | **0** | 0 | 0 | 0 | 0 |
| Muriate of Potash | 0 | 0 | 0 | 0 | 0 | 0 | **65** | 0 | 0 | 0 |
| Organic Fertilizer | 11 | 5 | 0 | 2 | 1 | 0 | 0 | **0** | 0 | 0 |
| Urea | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **31** | 0 |
| Water Retaining Fertilizer | 114 | 11 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | **0** |

*Mass collapse into Balanced NPK / General Purpose / Compost; Lime, Organic, Water Retaining never predicted.*

---

## 6. Sanity checks

- **10 example test rows** (true | Eval A | Eval B): Eval A matched the true label on all 10 shown;
  Eval B matched on only the NPK-driven classes (Muriate of Potash, DAP) and missed every
  Soil/Crop-dependent class.
- **Per-class P/R/F1 recomputed from the Eval A confusion matrix matches sklearn's
  `classification_report`** exactly (all 10 classes, `match=True`).

---

## 7. Honest caveats

1. **Eval A is optimistic (train/test overlap).** The shipped model scores 0.9694 on this dataset,
   but a *fresh* random forest trained on the same split reaches only 0.8113. The ~16-point gap
   indicates the shipped model was trained on this exact data, so Eval A reflects memorization, not
   generalization. **Do not report 0.9694 as real-world accuracy.**
2. **Production wiring ignores the actual Soil and Crop.** `onnx_recommender.py` hardcodes
   `moisture=50.0`, `soil="Loamy Soil"`, `crop="tomato"` and never reads the row's real Soil/Crop.
   Worse, `tomato` is absent from `crop_encoder.classes_`, so the `try/except` silently falls back
   to `crop_encoded=0`. Eval B (0.5677) quantifies the real deployed performance — a ~40-point drop
   from Eval A.
3. **Dataset appears synthetic.** Perfectly balanced crops (100 rows each), repeated moisture values,
   and physically impossible negative Potassium (71 rows) / Phosphorous (78 rows) values indicate
   generated data. Real-field performance will differ.
4. **Recommendation for the thesis:** report **Eval A (0.9694) as the model-artifact accuracy**, but
   pair it with **Eval B (0.5677) as the deployment caveat** and the **fresh-RF 0.8113 as the
   generalization ceiling**. The headline story is: *the model is strong, but the production wiring
   throws away its two most important features.*

---

## 8. Key takeaways

1. **The model artifact is high-quality:** 0.9694 accuracy / 0.9707 macro-F1 on its own dataset,
   with 6 of 10 classes at perfect F1.
2. **The production wiring is the bottleneck:** hardcoding Soil/Crop/Moisture drops deployed
   accuracy to 0.5677 — a 40-point loss caused entirely by feature engineering, not the model.
3. **`tomato` is a silent bug:** the hardcoded crop label doesn't exist in the encoder, triggering a
   silent fallback to class 0. This should be fixed (use a real crop or pass the actual crop).
4. **Generalization ceiling ≈ 0.81:** a fresh RF on a clean split gets 0.8113, so the true
   out-of-sample accuracy is far below the shipped model's 0.9694 — the shipped number is inflated
   by train/test overlap.
5. **The dataset is synthetic** (balanced crops, negative nutrients, repeated moisture), so all
   absolute numbers should be read as relative comparisons, not field-true performance.
