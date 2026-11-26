# ai — 동작 추론 (C팀)

## STEP 6: Django 없이 실제 MP4 로 예측해 보기

```bash
cd ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 버전은 모델을 만든 환경과 같게 고정
python inference.py 영상.mp4
```

결과 예 (S02 의 sit_stand 영상):

```json
{"predicted_label": "sit_stand", "confidence": 0.3167, "probabilities": {...}, "video": {"frames": 120, "seconds": 4.8}, "valid_frames": 120}
```

## inference.py 안의 함수 (위에서 아래 순서로 불림)

| 함수 | 받는 것 | 돌려주는 것 |
|---|---|---|
| `load_models()` | — | YOLO Pose, Random Forest, 학습 정보 |
| `extract_keypoints(영상, YOLO)` | MP4 경로 | Frame 마다 사람 1명의 관절 (17, 2) |
| `frame_feature(관절)` | 관절 (17, 2) | 숫자 38개 (정규화 34 + 각도 4) |
| `make_feature_vector(관절들, Frame 번호들)` | Frame 별 관절 | 30단계 × 38 = 1,140개 |
| `predict_video(영상, 모델)` | MP4 경로 | `predicted_label`, `confidence` 등 |

STEP 7 의 FastAPI 라우터는 `load_models()` 를 서버 시작 때 한 번, 요청마다 `predict_video()` 를 부른다.

## models/ (강사님 6일차 파일 그대로, 새로 학습하지 않음)

| 파일 | 내용 |
|---|---|
| `yolo11n-pose.pt` | 영상 → 관절 17개 |
| `rf_action_baseline.joblib` | 1,140개 → 동작 6개 (Random Forest, 300그루) |
| `rf_feature_columns.json` | 1,140개의 이름·순서 (`t00_nose_x` … `t29_right_knee_angle`) |
| `rf_training_info.json` | 동작 6개 이름, 30단계, 최소 10 Frame |
| `action_classes.csv` | 동작 번호 A001~A006 ↔ 이름 ↔ 설명 |

## 확인한 것

- 특징 계산이 원본 코드(`feature_from_keypoints`, `resample_sequence`)와 같은지: 같은 영상에서 1,140개 차이 0.0
- 실제 영상 18개 (`day05/videos` S01~S03 × 6동작): S01 6/6 · S02 3/6 · S03 6/6
  - S01 은 학습에 쓴 사람이라 확률 0.6 이상, 처음 보는 S02·S03 은 0.2~0.4 (학습 영상이 6개뿐이라 그렇다)
