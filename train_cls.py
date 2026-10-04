from ultralytics import YOLO
import sys

model = YOLO('yolo11n-cls.pt')
results = model.train(
    data='D:/Projects/veggiecare/Pest-Data',
    epochs=5,
    imgsz=224,
    batch=8,
    name='veggiecare_cls_test',
    project='D:/Projects/veggiecare/runs',
    patience=2,
    verbose=True
)
print('done')
