"""STEP 6 (C1): 기존 학습 모델로 실제 MP4 영상의 동작을 예측한다.

새로 학습하지 않는다. 강사님 6일차 파일을 그대로 쓴다.
  models/yolo11n-pose.pt          영상 → 사람 관절 17개 (YOLO Pose 가중치)
  models/rf_action_baseline.joblib 1,140개 숫자 → 동작 6개 중 하나 (학습 끝난 Random Forest)
  models/rf_feature_columns.json   1,140개 숫자의 이름과 순서
  models/rf_training_info.json     동작 이름, 30단계 등 학습 때 정보

흐름 (학습 때와 똑같이 계산해야 한다. 다르면 오류 없이 정확도만 떨어진다)
  영상 Frame → YOLO Pose → 사람 1명의 관절 17개(픽셀)
  → 정규화(골반 중심 · 어깨 폭) 34개 + 관절 각도 4개 = Frame 당 38개
  → 영상 전체를 30단계로 맞춤 → 30 × 38 = 1,140개 → Random Forest → 동작 · 확률

실행: python inference.py 영상.mp4
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import joblib
import numpy as np

MODELS = Path(__file__).parent / "models"

# 학습할 때 쓴 YOLO 설정 (subject10 configs 와 같은 값)
IMAGE_SIZE = 640          # YOLO 에 넣는 그림 크기
PERSON_CONFIDENCE = 0.25  # 이보다 낮은 사람 Box 는 버림
SEQUENCE_STEPS = 30       # 영상 길이와 상관없이 30단계로 맞춤
MIN_VALID_FRAMES = 10     # 관절이 잡힌 Frame 이 이보다 적으면 판단하지 않음

# COCO 관절 번호 (YOLO Pose 가 돌려주는 17개 순서)
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_ELBOW, RIGHT_ELBOW = 7, 8
LEFT_WRIST, RIGHT_WRIST = 9, 10
LEFT_HIP, RIGHT_HIP = 11, 12
LEFT_KNEE, RIGHT_KNEE = 13, 14
LEFT_ANKLE, RIGHT_ANKLE = 15, 16


class VideoError(Exception):
    """영상을 열 수 없거나, 사람이 충분히 안 보여서 판단할 수 없을 때"""


# ---------------------------------------------------------------
# 1) 모델 불러오기
# ---------------------------------------------------------------
def load_models() -> dict:
    """모델 파일 4개를 불러온다. 오래 걸리므로 서버에서는 시작할 때 한 번만 부른다."""
    from ultralytics import YOLO      # 무거운 라이브러리라 쓸 때 불러옴

    rf = joblib.load(MODELS / "rf_action_baseline.joblib")
    columns = json.loads((MODELS / "rf_feature_columns.json").read_text(encoding="utf-8"))
    info = json.loads((MODELS / "rf_training_info.json").read_text(encoding="utf-8"))

    # 학습 때와 입력 개수가 다르면 지금 바로 멈춘다 (나중에 엉뚱한 결과가 나오는 것보다 낫다)
    if not (rf.n_features_in_ == len(columns) == SEQUENCE_STEPS * 38):
        raise RuntimeError(f"모델 입력 {rf.n_features_in_}개, 특징 이름 {len(columns)}개 — 서로 다릅니다")

    return {"pose": YOLO(str(MODELS / "yolo11n-pose.pt")), "rf": rf, "info": info}


# ---------------------------------------------------------------
# 2) 영상 → Frame 마다 관절 좌표
# ---------------------------------------------------------------
def extract_keypoints(video_path: str | Path, pose_model) -> tuple[list[np.ndarray], list[int], dict]:
    """영상의 모든 Frame 에서 사람 1명(Box 확률이 가장 높은 사람)의 관절 17개를 뽑는다.

    돌려주는 값: (관절 목록, 그 Frame 번호 목록, 영상 정보)
      관절 하나는 (17, 2) 배열 = 17개 관절의 (x, y) 픽셀 좌표
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise VideoError("영상을 열 수 없습니다")

    keypoints_list, frame_ids = [], []
    frame_id = 0
    while True:
        ok, frame = cap.read()
        if not ok:                     # 영상 끝
            break
        result = pose_model.predict(frame, imgsz=IMAGE_SIZE, conf=PERSON_CONFIDENCE, verbose=False)[0]
        if result.boxes is not None and len(result.boxes) > 0 and result.keypoints is not None:
            best = int(np.argmax(result.boxes.conf.cpu().numpy()))     # 가장 확실한 사람
            keypoints_list.append(result.keypoints.xy[best].cpu().numpy())
            frame_ids.append(frame_id)
        frame_id += 1

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    video = {"frames": frame_id, "seconds": round(frame_id / fps, 2) if fps else 0.0}
    cap.release()
    if frame_id == 0:
        raise VideoError("영상을 열 수 없습니다")
    return keypoints_list, frame_ids, video


# ---------------------------------------------------------------
# 3) 관절 좌표 → 모델에 넣을 숫자 1,140개
# ---------------------------------------------------------------
def angle(a, b, c) -> float:
    """b 를 꼭짓점으로 a-b-c 가 이루는 각도(도). 예: 어깨-팔꿈치-손목 = 팔꿈치 각도"""
    ba, bc = np.asarray(a) - np.asarray(b), np.asarray(c) - np.asarray(b)
    na, nc = np.linalg.norm(ba), np.linalg.norm(bc)
    if na < 1e-8 or nc < 1e-8:
        return float("nan")
    cos = np.clip(np.dot(ba, bc) / (na * nc), -1.0, 1.0)
    return math.degrees(math.acos(float(cos)))


def frame_feature(kp: np.ndarray) -> np.ndarray | None:
    """Frame 하나의 관절 (17, 2) → 숫자 38개. 계산이 안 되면 None (그 Frame 은 버림)"""
    kp = np.asarray(kp, dtype=np.float32)
    if kp.shape != (17, 2) or not np.all(np.isfinite(kp)):
        return None

    # 사람이 화면 어디에 있든, 크든 작든 같은 숫자가 나오도록
    center = (kp[LEFT_HIP] + kp[RIGHT_HIP]) / 2.0                     # 골반 가운데를 (0, 0) 으로
    scale = float(np.linalg.norm(kp[LEFT_SHOULDER] - kp[RIGHT_SHOULDER]))  # 어깨 폭을 1 로
    if scale < 1e-8:
        return None
    normalized = (kp - center) / scale                                 # 34개 (17관절 × x, y)

    angles = [
        angle(kp[LEFT_SHOULDER], kp[LEFT_ELBOW], kp[LEFT_WRIST]),     # 왼 팔꿈치
        angle(kp[RIGHT_SHOULDER], kp[RIGHT_ELBOW], kp[RIGHT_WRIST]),  # 오른 팔꿈치
        angle(kp[LEFT_HIP], kp[LEFT_KNEE], kp[LEFT_ANKLE]),           # 왼 무릎
        angle(kp[RIGHT_HIP], kp[RIGHT_KNEE], kp[RIGHT_ANKLE]),        # 오른 무릎
    ]
    feature = np.concatenate([normalized.reshape(-1), angles]).astype(np.float32)   # 34 + 4 = 38
    return feature if np.all(np.isfinite(feature)) else None


def make_feature_vector(keypoints_list: list[np.ndarray], frame_ids: list[int]) -> np.ndarray:
    """영상 전체의 Frame 별 38개 → 30단계로 맞춘 뒤 한 줄로 펴서 1,140개"""
    feats, ids = [], []
    for kp, fid in zip(keypoints_list, frame_ids):
        f = frame_feature(kp)
        if f is not None:
            feats.append(f)
            ids.append(fid)
    if len(feats) < MIN_VALID_FRAMES:
        raise VideoError(f"관절이 잘 보인 Frame 이 {len(feats)}개뿐이라 판단할 수 없습니다 (최소 {MIN_VALID_FRAMES}개)")

    feats = np.stack(feats)                             # (유효 Frame 수, 38)
    ids = np.asarray(ids, dtype=np.float32)
    x_old = (ids - ids[0]) / (ids[-1] - ids[0])         # 영상 처음 0 ~ 끝 1
    x_new = np.linspace(0.0, 1.0, SEQUENCE_STEPS)       # 30단계
    seq = np.stack([np.interp(x_new, x_old, feats[:, j]) for j in range(feats.shape[1])], axis=1)
    return seq.astype(np.float32).reshape(1, -1)        # (1, 1140) — 순서: t00 관절들, t01 관절들, ...


# ---------------------------------------------------------------
# 4) 예측
# ---------------------------------------------------------------
def predict_video(video_path: str | Path, models: dict) -> dict:
    """영상 하나 → {"predicted_label": 동작 이름, "confidence": 확률 0~1, ...}"""
    keypoints_list, frame_ids, video = extract_keypoints(video_path, models["pose"])
    x = make_feature_vector(keypoints_list, frame_ids)

    probs = models["rf"].predict_proba(x)[0]            # 동작 6개 각각의 확률
    best = int(np.argmax(probs))
    return {
        "predicted_label": str(models["rf"].classes_[best]),
        "confidence": round(float(probs[best]), 4),
        "probabilities": {str(c): round(float(p), 4) for c, p in zip(models["rf"].classes_, probs)},
        "video": video,
        "valid_frames": len(frame_ids),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("사용법: python inference.py 영상.mp4")
    result = predict_video(sys.argv[1], load_models())
    print(json.dumps(result, ensure_ascii=False, indent=2))
