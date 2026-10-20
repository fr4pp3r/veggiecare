# VeggieCare Pest Classifier — Validation Evaluation Report

**Checkpoint evaluated:** `D:\Projects\veggiecare\runs\veggiecare_cls_v1\weights\best.pt`
(yolo11n-cls architecture, 9-class classifier at 224 px)

**Training run:** `veggiecare_cls_v1` (`runs/veggiecare_cls_v1`), trained for **1 logged epoch**
(from `runs/veggiecare_cls_v1/args.yaml`: `model: yolo11n-cls.pt`, `data: D:/Projects/veggiecare/Pest-Data`, `epochs: 30`, `imgsz: 224`, `warmup_epochs: 3.0`).

**Dataset:** `D:\Projects\veggiecare\Pest-Data`
**Split used:** the dataset contains only `train/` (2,700 images) and `test/` (450 images) — there is **no `val/` split**. The distributed validator fell back to the `test/` directory for the validation split (standard Ultralytics behavior when no val split is defined: `WARNING ⚠️ no test..., using 'test'... missing val, using test` path in `check_cls_dataset`). All numbers below are therefore on the **450-image test split**, not on training data.

- Classes (9, alphabetical order as loaded): aphids, armyworm, beetle, bollworm, grasshopper, mites, mosquito, sawfly, stem_borer
- Per-class support on the evaluated split: **50 images per class** (perfectly balanced).

## 1. Headline results

| Metric | Value |
|---|---|
| Top-1 accuracy | **0.8711** (87.11 %) |
| Top-5 accuracy | **1.0000** (100 % — with only 9 classes this means every single test image had its true label in the top half of the ranking) |
| Macro-F1 | **0.8685** |
| Weighted-F1 | **0.8685** (equal to macro-F1 because per-class supports are identical) |
| Micro precision (= top-1 here) | 0.8711 |
| Total val images | **450** |

Speed (CPU, Intel x86_64 host): 0.0 ms preprocess, **4.3 ms inference**, 0.0 ms postprocess per image.

## 2. Per-class table

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| aphids | 0.9362 | 0.8800 | 0.9072 | 50 |
| armyworm | 0.8043 | 0.7400 | 0.7708 | 50 |
| beetle | 0.9259 | 1.0000 | 0.9615 | 50 |
| bollworm | 0.7460 | 0.9400 | 0.8319 | 50 |
| grasshopper | 0.9592 | 0.9400 | 0.9495 | 50 |
| mites | 0.8654 | 0.9000 | 0.8824 | 50 |
| mosquito | 0.9615 | 1.0000 | 0.9804 | 50 |
| sawfly | 0.8158 | 0.6200 | 0.7045 | 50 |
| stem_borer | 0.8367 | 0.8200 | 0.8283 | 50 |
| **all (macro)** | 0.8720 | 0.8711 | **0.8685** | 450 |

Recall range: **0.6200 (sawfly)** to **1.0000 (beetle, mosquito)**. Precision range: 0.7460 (bollworm) to 0.9615 (mosquito).

**Observations** (confusion directions below are written **true → predicted**)
- Sawfly is the clear weak spot: recall 0.62 (FN=19), and its losses also degrade armyworm's precision — 6 of armyworm's 9 FPs are sawfly images (plus 2 bollworm, 1 grasshopper).
- Armyworm bleeds in both directions: FN=13 (7 of its images are called bollworm, 3 called stem_borer), and P=0.80 due to absorbing 9 non-armyworm images.
- Bollworm has high recall (0.94, FN=3) but the worst precision (0.75): FP=16, the largest in the matrix — 7 armyworm, 2 aphids, 2 mites, 2 sawfly and 3 stem_borer images get labeled bollworm.
- Beetle and mosquito are effectively solved on this split (F1 ≈ 0.96–0.98, FN = 0). Their only errors are FPs (beetle: 1 sawfly + 3 stem_borer images; mosquito: 1 mites + 1 sawfly image).
- Macro-F1 (0.8685) sits below top-1 (0.8711): the F1 penalty concentrates on the weakest classes (sawfly 0.70), exactly where per-class failures hurt for deployment monitoring.

### 2.1 Per-class one-vs-rest counts (derived from the confusion matrix)

For each class c: TP = diagonal cell (true c, predicted c); FP = rest of the predicted row (non-c images labeled c); FN = rest of the true column (c images labeled something else); TN = everything else (450 − TP − FP − FN).

| Class | TP | FP | FN | TN | TP+FN (support) |
|---|---:|---:|---:|---:|---:|
| aphids | 44 | 3 | 6 | 397 | 50 |
| armyworm | 37 | 9 | 13 | 391 | 50 |
| beetle | 50 | 4 | 0 | 396 | 50 |
| bollworm | 47 | 16 | 3 | 384 | 50 |
| grasshopper | 47 | 2 | 3 | 398 | 50 |
| mites | 45 | 7 | 5 | 393 | 50 |
| mosquito | 50 | 2 | 0 | 398 | 50 |
| sawfly | 31 | 7 | 19 | 393 | 50 |
| stem_borer | 41 | 8 | 9 | 392 | 50 |
| **total** | **392** | **58** | **58** | — | **450** |

Sanity check: total TP (392) / 450 = 0.8711 = top-1 accuracy; TP+FN = recall denominator; TP+FP = precision denominator.

In plain English, for **aphids**: TP = 44 means 44 aphid images were correctly identified as aphids; FP = 3 means 3 non-aphid images were wrongly labeled as aphids (all 3 were sawfly images); FN = 6 means 6 aphid images were missed (3 called mites, 2 called bollworm, 1 called grasshopper); TN = 397 means 397 non-aphid images were correctly *not* labeled aphids.

For the weakest class, **sawfly**: TP = 31 means 31 sawfly images were correctly identified; FP = 7 means 7 non-sawfly images were wrongly labeled sawfly (4 stem_borer, 2 armyworm, 1 mites); FN = 19 means 19 of the 50 sawfly images were missed — the biggest single bucket being misread as armyworm (6) and stem_borer (4), with the remainder scattered across aphids, bollworm, beetle, grasshopper, mites and mosquito (14 total); TN = 393.

## 3. Confusion matrix (raw counts)

Rows = predicted class, columns = true class. Sum of every column = 50.

| Pred \ True | aphids | armyworm | beetle | bollworm | grasshopper | mites | mosquito | sawfly | stem_borer | row_total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aphids | 44 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 47 |
| armyworm | 0 | 37 | 0 | 2 | 1 | 0 | 0 | 6 | 0 | 46 |
| beetle | 0 | 0 | 50 | 0 | 0 | 0 | 0 | 1 | 3 | 54 |
| bollworm | 2 | 7 | 0 | 47 | 0 | 2 | 0 | 2 | 3 | 63 |
| grasshopper | 1 | 0 | 0 | 0 | 47 | 0 | 0 | 1 | 0 | 49 |
| mites | 3 | 1 | 0 | 0 | 1 | 45 | 0 | 1 | 1 | 52 |
| mosquito | 0 | 0 | 0 | 0 | 0 | 1 | 50 | 1 | 0 | 52 |
| sawfly | 0 | 2 | 0 | 0 | 1 | 2 | 0 | 31 | 2 | 38 |
| stem_borer | 0 | 3 | 0 | 1 | 0 | 0 | 0 | 4 | 41 | 49 |
| col_total | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 450 |

(Unformatted copy also saved as `best_pt_confusion.csv`. Rendered plots: `best_pt/confusion_matrix.png` and `best_pt/confusion_matrix_normalized.png`.)

Dominant confusions (directions written **true → predicted**):
- **sawfly → armyworm (6)** and **armyworm → bollworm (7)** — the caterpillar-family drift: 6 sawfly and 7 more armyworm images end up labeled bollworm or armyworm respectively.
- **sawfly's 19 misses** scatter widely: 6 to armyworm, 4 to stem_borer, 3 to aphids, 2 to bollworm, and 1 each to beetle, grasshopper, mites, mosquito — the most diluted class in the matrix.
- **stem_borer → beetle (3)** and **sawfly → stem_borer (4)** — cross-family confusions between elongated/larval-looking pests.
- **aphids → mites (3)** — the remaining small-cluster leak (only 2 mites and 1 grasshopper images cross the other way).

## 4. Environment and command

| Package | Version |
|---|---|
| ultralytics | 8.4.182 |
| torch (CPU-only wheel) | 2.14.1+cpu |
| torchvision | 0.29.1+cpu |
| numpy | 2.5.2 |
| opencv (headless, deliberate) | 5.0.0.93 |
| pillow | 12.3.0 |
| scipy | 1.18.1 |
| matplotlib | 3.11.2 |

- Python: 3.14.7 in a throwaway venv (`C:\Users\Nathaniel\AppData\Local\Temp\veggiecare-eval\.venv`); wheels only, nothing compiled from source.
- Device: `cpu`.

**Command used** (the installed ultralytics 8.4.182 build has no `python -m ultralytics` `__main__` / no `ultralytics.cli` module, so the equivalent API call was used):

```python
from ultralytics import YOLO
model = YOLO(r"D:\Projects\veggiecare\runs\veggiecare_cls_v1\weights\best.pt")
metrics = model.val(
    data=r"D:\Projects\veggiecare\Pest-Data",
    imgsz=224,
    device="cpu",
    project=r"D:\Projects\veggiecare\runs\eval_v1",
    name="best_pt",
    plots=True,
    save_json=False,
    verbose=True,
)
```

(Per-class precision/recall/F1/support were computed from the validator's confusion matrix, indexed `[predicted, true]`; Ultralytics' `ClassifyMetrics` itself only reports top-1/top-5.)

- Raw console output saved verbatim to: `runs\eval_v1\best_pt_stdout.txt`
- Machine-readable results: `runs\eval_v1\best_pt_metrics.json`, `runs\eval_v1\best_pt_confusion.csv`

## 5. Artifacts

| Path | Purpose |
|---|---|
| `runs\eval_v1\best_pt\confusion_matrix.png` | Raw counts confusion matrix |
| `runs\eval_v1\best_pt\confusion_matrix_normalized.png` | Row-normalized confusion matrix |
| `runs\eval_v1\best_pt\val_batch{0,1,2}_{labels,pred}.jpg` | Sample val batches with GT labels / model predictions |
| `runs\eval_v1\best_pt_stdout.txt` | Verbatim raw console output of the run |
| `runs\eval_v1\best_pt_metrics.json` | All headline + per-class metrics as JSON |
| `runs\eval_v1\best_pt_confusion.csv` | Confusion matrix as CSV |

No training or dataset files were modified; the project `.venv` was untouched (separate throwaway venv).
