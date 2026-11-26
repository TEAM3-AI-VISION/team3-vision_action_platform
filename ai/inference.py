"""무인 상점 이상행동 17동작 추론 — MP4 한 개 → 사람별 동작·신뢰도.

    from inference import predict_video
    result = predict_video("video.mp4")

영상 → YOLO11m Pose + ByteTrack(사람별 번호) → Frame 마다 특징 40개
     → 8초 창(4초씩 이동) → 30단계로 보간 → 변화량·요약 통계를 더해 2,560개
     → ExtraTrees 확률과 1D-CNN 확률의 평균 → 창 평균 → 사람별 1등·top3

특징 계산은 학습 때와 같아야 한다. 하나라도 다르면 모델이 엉뚱한 답을 낸다.
실행: python inference.py video.mp4
"""
from __future__ import annotations

import json
import math
import sys
import time
from functools import lru_cache
from pathlib import Path

import cv2
import joblib
import numpy as np
import torch
from torch import nn

MODELS = Path(__file__).resolve().parent / "models"

IMAGE_SIZE = 640            # YOLO 입력 크기
PERSON_CONF = 0.25          # 사람으로 볼 최소 Confidence
MAX_VIDEO_SECONDS = 60      # 이보다 긴 영상은 앞 60초만
MAX_PROCESS_FPS = 30        # 60fps 영상은 2 Frame 마다 1개
WINDOW_SECONDS = 8.0        # 학습 영상 중앙값 5.5초 → 8초 창이면 동작 하나가 들어간다
WINDOW_STRIDE = 4.0
MIN_TRACK_SECONDS = 1.0     # 1초도 안 보인 사람은 판단하지 않음
MIN_TRACK_KP_CONF = 0.4     # 관절 Confidence 평균이 낮으면 사람이 아닌 물체(의자·가방)
UNCERTAIN_BELOW = 0.3       # 1등 확률이 이보다 낮으면 불확실

# COCO 관절 번호
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16


# ---------------------------------------------------------------- 1. 모델 불러오기
class CNN(nn.Module):
    """시간 방향 1D-CNN: (관절 40 + 변화량 40 채널, 30단계) → 17동작"""

    def __init__(self, c_in: int, n_cls: int, w: int = 64, p: float = 0.3):
        super().__init__()

        def block(a, b, k):
            return nn.Sequential(nn.Conv1d(a, b, k, padding=k // 2), nn.BatchNorm1d(b), nn.ReLU())
        self.f = nn.Sequential(block(c_in, w, 5), block(w, w, 5), nn.Dropout(p), block(w, 2 * w, 3))
        self.head = nn.Sequential(nn.Dropout(p), nn.Linear(4 * w, n_cls))

    def forward(self, x):
        h = self.f(x)
        return self.head(torch.cat([h.mean(2), h.amax(2)], 1))


@lru_cache(maxsize=1)
def load_models() -> dict:
    """처음 한 번만 읽고 이후 요청은 재사용. 파일끼리 맞지 않으면 바로 오류."""
    from ultralytics import YOLO

    info = json.loads((MODELS / "training_info.json").read_text(encoding="utf-8"))
    columns = json.loads((MODELS / "feature_columns.json").read_text(encoding="utf-8"))
    trees = joblib.load(MODELS / "action_model.joblib")        # 직접 학습해 저장한 파일만 연다
    if trees.n_features_in_ != len(columns) or [str(c) for c in trees.classes_] != info["classes"]:
        raise ValueError("action_model.joblib 과 feature_columns.json·training_info.json 이 맞지 않습니다.")

    d = torch.load(MODELS / info["ensemble"]["cnn_file"], map_location="cpu", weights_only=True)
    cnns = []
    for state in d["state_dicts"]:
        m = CNN(2 * d["n_frame"], d["n_cls"], w=d["width"])
        m.load_state_dict(state)
        cnns.append(m.eval())

    return {"info": info, "trees": trees, "cnns": cnns, "mu": d["mu"].numpy(), "sd": d["sd"].numpy(),
            "pose": YOLO(str(MODELS / "yolo11m-pose.pt")),
            "device": 0 if torch.cuda.is_available() else "cpu"}


# ---------------------------------------------------------------- 2. 관절 → 특징 40개
def angle(a, b, c) -> float:
    """b 를 꼭짓점으로 한 a-b-c 각도(도)"""
    ba, bc = a - b, c - b
    na, nc = np.linalg.norm(ba), np.linalg.norm(bc)
    if na < 1e-8 or nc < 1e-8:
        return float("nan")
    return math.degrees(math.acos(float(np.clip(np.dot(ba, bc) / (na * nc), -1.0, 1.0))))


def frame_features(xy: np.ndarray, img_w: int, img_h: int) -> tuple[np.ndarray, bool]:
    """관절 17×2(px) → (40개, 유효 여부)
    34 = 골반 중심 기준 x·y ÷ 몸 크기 (카메라와의 거리에 상관없이 같은 값)
     4 = 팔꿈치·무릎 각도
     2 = 골반의 화면 위치 0~1 (넘어지면 아래로)
    """
    kp = np.asarray(xy, dtype=np.float32)
    values = np.full(40, np.nan)
    hip = (kp[L_HIP] + kp[R_HIP]) / 2.0
    shoulder = (kp[L_SH] + kp[R_SH]) / 2.0
    # 몸 크기 = 몸통 길이와 (어깨 폭 ÷ 0.70) 중 큰 쪽 — 옆모습·누운 자세에서도 0 에 가까워지지 않게
    scale = max(float(np.linalg.norm(hip - shoulder)), float(np.linalg.norm(kp[L_SH] - kp[R_SH])) / 0.70)
    if scale < 5.0 or float(np.linalg.norm(kp[L_SH] - kp[R_SH])) < 1e-8:
        return values, False
    angles = [angle(kp[L_SH], kp[L_EL], kp[L_WR]), angle(kp[R_SH], kp[R_EL], kp[R_WR]),
              angle(kp[L_HIP], kp[L_KN], kp[L_AN]), angle(kp[R_HIP], kp[R_KN], kp[R_AN])]
    values[:34] = np.round(((kp - hip) / scale).reshape(-1), 6)
    values[34:38] = np.round(angles, 6)
    values[38] = round(float(hip[0]) / img_w, 4)
    values[39] = round(float(hip[1]) / img_h, 4)
    return values, bool(np.all(np.isfinite(values)))


def extract_people(video_path: str, m: dict) -> tuple[dict, dict]:
    """영상 → {사람 번호: {"t": 시간들, "x": 특징들, "ok": 유효들, "conf": 관절 Confidence 평균들}}"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("영상 파일을 열 수 없습니다.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps != fps or fps > 240:
        fps = 30.0
    step = max(1, round(fps / MAX_PROCESS_FPS))
    people: dict[int, dict] = {}
    n = used = w = h = 0
    while n < MAX_VIDEO_SECONDS * fps:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        if (n - 1) % step:
            continue
        used += 1
        h, w = frame.shape[:2]
        r = m["pose"].track(source=frame, persist=used > 1, tracker="bytetrack.yaml", imgsz=IMAGE_SIZE,
                            conf=PERSON_CONF, device=m["device"], verbose=False)[0]
        if r.boxes is None or r.boxes.id is None or r.keypoints is None:
            continue
        for tid, xy, conf in zip(r.boxes.id.int().tolist(), r.keypoints.xy.cpu().numpy(),
                                 r.keypoints.conf.cpu().numpy()):
            values, valid = frame_features(xy, w, h)
            p = people.setdefault(int(tid), {"t": [], "x": [], "ok": [], "conf": []})
            p["t"].append((n - 1) / fps)
            p["x"].append(values)
            p["ok"].append(valid)
            p["conf"].append(float(np.mean(conf)))
    cap.release()
    if n == 0:
        raise ValueError("영상에서 Frame 을 읽지 못했습니다.")
    return people, {"frames": n, "fps": round(fps, 3), "seconds": round(n / fps, 2), "width": w, "height": h}


# ---------------------------------------------------------------- 3. 특징 → 동작 확률
def predict_proba(seq: np.ndarray, m: dict) -> np.ndarray:
    """30단계 × 40 (1,200개) → 17동작 확률"""
    steps = m["info"]["sequence_steps"]
    s = seq.reshape(1, steps, 40)
    # ExtraTrees: 1,200 + 변화량 29×40 + 단계마다 평균·표준편차·최소·최대·범위 5×40 = 2,560
    lo, hi = s.min(1), s.max(1)
    x = np.hstack([seq, np.diff(s, axis=1).reshape(1, -1), s.mean(1), s.std(1), lo, hi, hi - lo])
    p_trees = m["trees"].predict_proba(x)
    # 1D-CNN: 채널 = 관절 40 + 변화량 40, 시간 30
    v = np.diff(s, axis=1, prepend=s[:, :1])
    c = np.concatenate([s, v], 2).transpose(0, 2, 1).astype(np.float32)
    with torch.no_grad():
        t = torch.tensor((c - m["mu"]) / m["sd"])
        p_cnn = np.mean([torch.softmax(net(t), 1).numpy() for net in m["cnns"]], 0)
    return ((p_trees + p_cnn) / 2)[0]


def predict_person(p: dict, m: dict) -> dict:
    info = m["info"]
    steps, rule = info["sequence_steps"], info["valid_frame_rule"]
    t, x, ok = np.asarray(p["t"]), np.stack(p["x"]), np.asarray(p["ok"])
    span = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    quality = {"frames": len(t), "valid_frames": int(ok.sum()), "track_seconds": round(span, 2),
               "mean_keypoint_conf": round(float(np.mean(p["conf"])), 3)}
    if span < MIN_TRACK_SECONDS:
        return {"skipped": True, "reason": f"화면에 {span:.1f}초만 보임", **quality}
    if quality["mean_keypoint_conf"] < MIN_TRACK_KP_CONF:
        return {"skipped": True, "reason": "관절 Confidence 가 낮아 사람이 아닌 물체로 보임", **quality}

    # 8초 이하면 통째로 창 하나, 길면 8초 창을 4초씩 밀며 여러 개 (마지막 창은 끝에 맞춤)
    if span <= WINDOW_SECONDS:
        windows = [(t[0], t[-1])]
    else:
        starts = list(np.arange(t[0], t[-1] - WINDOW_SECONDS, WINDOW_STRIDE)) + [t[-1] - WINDOW_SECONDS]
        windows = [(float(s), float(s) + WINDOW_SECONDS) for s in starts]

    probs = []
    for s, e in windows:
        inside = (t >= s - 1e-9) & (t <= e + 1e-9)
        sel = inside & ok
        if sel.sum() < rule["min_valid_frames"] or sel.sum() / max(inside.sum(), 1) < rule["min_valid_ratio"]:
            continue                                       # 관절이 잘 안 보인 창은 버림
        ts, xs = t[sel], x[sel]
        grid = np.linspace(0.0, 1.0, steps)                # 창 길이와 상관없이 30단계로 고르게
        u = (ts - ts[0]) / (ts[-1] - ts[0])
        seq = np.stack([np.interp(grid, u, xs[:, j]) for j in range(40)], 1).reshape(1, -1)
        probs.append(predict_proba(seq, m))
    if not probs:
        return {"skipped": True, "reason": "관절이 잘 보이는 Frame 이 부족함", **quality}

    mean_p = np.mean(probs, axis=0)
    order = np.argsort(mean_p)[::-1]
    classes, names = info["classes"], info["class_info"]

    def item(i):
        aid = classes[i]
        return {"action_id": aid, "action_name_ko": names[aid]["name_ko"], "probability": round(float(mean_p[i]), 4)}

    best = item(order[0])
    return {"skipped": False, "action_id": best["action_id"], "action_name_ko": best["action_name_ko"],
            "category": names[best["action_id"]].get("category", ""), "confidence": best["probability"],
            "uncertain": best["probability"] < UNCERTAIN_BELOW, "window_count": len(probs),
            "top3": [item(int(i)) for i in order[:3]], **quality}


# ---------------------------------------------------------------- 4. 영상 하나 → 결과
def predict_video(video_path: str) -> dict:
    started = time.perf_counter()
    m = load_models()
    people, video = extract_people(video_path, m)
    predictions, skipped = [], []
    for tid in sorted(people):
        r = predict_person(people[tid], m)
        (skipped if r.pop("skipped") else predictions).append({"track_id": tid, **r})
    if not people:
        message = "영상에서 사람을 찾지 못했습니다."
    elif not predictions:
        message = f"사람은 보였지만 동작을 판단하지 못했습니다: {skipped[0]['reason']}."
    else:
        message = ""
    return {"message": message, "video": video, "processing_seconds": round(time.perf_counter() - started, 2),
            "predictions": predictions, "skipped_tracks": skipped}


if __name__ == "__main__":
    out = predict_video(sys.argv[1])
    for p in out["predictions"]:
        top3 = ", ".join(f"{t['action_id']} {t['probability']:.2f}" for t in p["top3"])
        print(f"사람 {p['track_id']}: {p['action_id']} {p['action_name_ko']} {p['confidence']:.2f}"
              f"{' (불확실)' if p['uncertain'] else ''}  | top3 {top3}")
    print(out["message"] or "", f"처리 {out['processing_seconds']}초")
