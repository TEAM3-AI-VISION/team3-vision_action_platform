"""무인 상점 이상행동 17동작 추론 — MP4 한 개 → 사람별 동작·신뢰도.

    from inference import predict_video
    result = predict_video("video.mp4")

영상 → YOLO11m Pose + ByteTrack(사람별 번호) → Frame 마다 특징 40개
     → 8초 창(4초씩 이동) → 30단계로 보간 → 변화량·요약 통계·움직임 요약 86개를 더해 2,646개
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
    cnns = []                                                  # 1D-CNN 은 난수만 바꿔 3번 학습 → 3개 확률을 평균
    for state in d["state_dicts"]:
        m = CNN(2 * d["n_frame"], d["n_cls"], w=d["width"])
        m.load_state_dict(state)
        cnns.append(m.eval())

    return {"info": info, "trees": trees, "cnns": cnns, "mu": d["mu"].numpy(), "sd": d["sd"].numpy(),
            "pose": YOLO(str(MODELS / "yolo11m-pose.pt")),
            "device": 0 if torch.cuda.is_available() else "cpu"}     # GPU 가 있으면 0번 GPU, 없으면 CPU


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
    step = max(1, round(fps / MAX_PROCESS_FPS))                 # 60fps 영상은 2장 중 1장만 → 처리 시간 절반
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
        # persist: 두 번째 Frame 부터는 앞 Frame 의 사람 번호를 이어 받는다 (ByteTrack)
        r = m["pose"].track(source=frame, persist=used > 1, tracker="bytetrack.yaml", imgsz=IMAGE_SIZE,
                            conf=PERSON_CONF, device=m["device"], verbose=False)[0]
        if r.boxes is None or r.boxes.id is None or r.keypoints is None:
            continue                                            # 이 Frame 에는 사람이 없음
        for tid, xy, conf in zip(r.boxes.id.int().tolist(), r.keypoints.xy.cpu().numpy(),
                                 r.keypoints.conf.cpu().numpy()):
            values, valid = frame_features(xy, w, h)
            p = people.setdefault(int(tid), {"t": [], "x": [], "ok": [], "conf": []})
            p["t"].append((n - 1) / fps)                          # 이 Frame 의 시간(초)
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
    # ExtraTrees: 1,200 + 변화량 29×40 + 평균·표준편차·최소·최대·범위 5×40 + 움직임 요약 86 = 2,646
    lo, hi = s.min(1), s.max(1)
    kin = kinematic_summary(s[0, :, :38].reshape(-1))[None]          # 움직임 요약 86개 (아래 3-1)
    x = np.hstack([seq, np.diff(s, axis=1).reshape(1, -1), s.mean(1), s.std(1), lo, hi, hi - lo, kin])
    p_trees = m["trees"].predict_proba(x)
    # 1D-CNN: 채널 = 관절 40 + 변화량 40, 시간 30
    v = np.diff(s, axis=1, prepend=s[:, :1])
    c = np.concatenate([s, v], 2).transpose(0, 2, 1).astype(np.float32)
    with torch.no_grad():
        t = torch.tensor((c - m["mu"]) / m["sd"])             # 학습 때 평균·표준편차로 크기를 맞춤
        p_cnn = np.mean([torch.softmax(net(t), 1).numpy() for net in m["cnns"]], 0)
    return ((p_trees + p_cnn) / 2)[0]


# ---------------------------------------------------------------- 3-1. 움직임 요약 86개
# 2,560개는 관절 값 하나하나를 본다. 86개는 두 손이 같이 움직이는지(상관), 팔을 몇 번 폈다 굽혔는지(피크 수),
# 손이 코 위로 갔는지, 몸이 옆으로 기우는지처럼 동작을 가르는 관계·횟수를 요약한다.
# 다른 카메라 구도의 현장 영상에서 14/15 → 15/15 (앞으로 넘어지기를 옆으로 쓰러지기와 구분).
def find_peaks(x, height=None, prominence=None):
    """scipy.signal.find_peaks(x, height=, prominence=) 와 같은 결과 — numpy 만으로 (1D, 최솟값 조건만)"""
    x = np.asarray(x)
    n = len(x)
    peaks = []
    i = 1
    while i < n - 1:
        if x[i - 1] < x[i]:
            ahead = i + 1
            while ahead < n - 1 and x[ahead] == x[i]:
                ahead += 1
            if x[ahead] < x[i]:
                peaks.append((i + ahead - 1) // 2)
                i = ahead
        i += 1
    if height is not None:
        peaks = [p for p in peaks if x[p] >= height]
    if prominence is not None:
        keep = []
        for p in peaks:
            left_min, j = x[p], p
            while j >= 0 and x[j] <= x[p]:
                left_min = min(left_min, x[j])
                j -= 1
            right_min, j = x[p], p
            while j <= n - 1 and x[j] <= x[p]:
                right_min = min(right_min, x[j])
                j += 1
            if x[p] - max(left_min, right_min) >= prominence:
                keep.append(p)
        peaks = keep
    return np.asarray(peaks, dtype=np.intp), {}


def kinematic_summary(seq_1140: np.ndarray) -> np.ndarray:
    """30단계 × 38 (관절 34 + 각도 4) → 86개. 동작 시작 시점·좌우·카메라 각도에 덜 민감한 요약."""
    frames = np.asarray(seq_1140, dtype=np.float32).reshape((30, 38))
    coords = frames[:, :34].reshape((30, 17, 2))
    sh_mid = (coords[:, 5, :] + coords[:, 6, :]) / 2.0
    torso_s = np.maximum(np.linalg.norm(sh_mid, axis=1), 0.40)[:, None, None]
    tc = coords / torso_s

    l_elb, r_elb = frames[:, 34], frames[:, 35]
    l_knee, r_knee = frames[:, 36], frames[:, 37]
    avg_elb = (l_elb + r_elb) / 2.0
    min_elb_lr = np.minimum(l_elb, r_elb)
    max_elb_lr = np.maximum(l_elb, r_elb)
    avg_knee = (l_knee + r_knee) / 2.0
    min_knee_lr = np.minimum(l_knee, r_knee)
    max_knee_lr = np.maximum(l_knee, r_knee)

    sp = (tc[:, 5, :] + tc[:, 6, :]) / 2.0
    sp_tilt = np.degrees(np.arctan2(np.abs(sp[:, 0]), np.maximum(-sp[:, 1], 1e-3)))
    sh_y, sh_x = sp[:, 1], sp[:, 0]

    nose_x, nose_y = tc[:, 0, 0], tc[:, 0, 1]
    nose_rel_sh_y = nose_y - sh_y

    l_wx, l_wy = tc[:, 9, 0], tc[:, 9, 1]
    r_wx, r_wy = tc[:, 10, 0], tc[:, 10, 1]
    w_mid_x, w_mid_y = (l_wx + r_wx) / 2.0, (l_wy + r_wy) / 2.0
    w_dist = np.hypot(l_wx - r_wx, l_wy - r_wy)

    l_wy_sh, r_wy_sh = l_wy - sh_y, r_wy - sh_y
    w_rel_nose_y = w_mid_y - nose_y
    min_single_w_rel_nose = np.minimum(l_wy, r_wy) - nose_y

    act_wy_span = max(float(np.ptp(l_wy)), float(np.ptp(r_wy)))
    sec_wy_span = min(float(np.ptp(l_wy)), float(np.ptp(r_wy)))
    two_hand_y_ratio = sec_wy_span / max(act_wy_span, 1e-4)

    act_wx_span = max(float(np.ptp(l_wx)), float(np.ptp(r_wx)))
    sec_wx_span = min(float(np.ptp(l_wx)), float(np.ptp(r_wx)))
    two_hand_x_ratio = sec_wx_span / max(act_wx_span, 1e-4)

    wy_corr = float(np.corrcoef(l_wy, r_wy)[0, 1]) if (np.std(l_wy) > 1e-3 and np.std(r_wy) > 1e-3) else 0.0
    wx_corr = float(np.corrcoef(l_wx, r_wx)[0, 1]) if (np.std(l_wx) > 1e-3 and np.std(r_wx) > 1e-3) else 0.0

    l_kx, l_ky = tc[:, 13, 0], tc[:, 13, 1]
    r_kx, r_ky = tc[:, 14, 0], tc[:, 14, 1]
    l_ax, l_ay = tc[:, 15, 0], tc[:, 15, 1]
    r_ax, r_ay = tc[:, 16, 0], tc[:, 16, 1]
    ank_mid_y = (l_ay + r_ay) / 2.0
    knee_mid_y = (l_ky + r_ky) / 2.0
    ank_asym_y = np.abs(l_ay - r_ay)
    ank_sep_2d = np.hypot(l_ax - r_ax, l_ay - r_ay)
    knee_asym_y = np.abs(l_ky - r_ky)

    # 측면 낙상(fall_sideways) vs 전후방 낙상 구분용 좌우 롤(Roll) 비대칭 특징
    sh_roll_y = np.abs(tc[:, 5, 1] - tc[:, 6, 1])
    hip_roll_y = np.abs(tc[:, 11, 1] - tc[:, 12, 1])
    ear_roll_y = np.abs(tc[:, 3, 1] - tc[:, 4, 1])
    sh_width_tc = np.hypot(tc[:, 5, 0] - tc[:, 6, 0], tc[:, 5, 1] - tc[:, 6, 1])

    d_tc = np.diff(tc, axis=0)
    w_speed = np.maximum(np.linalg.norm(d_tc[:, 9, :], axis=1), np.linalg.norm(d_tc[:, 10, :], axis=1))
    a_speed = np.maximum(np.linalg.norm(d_tc[:, 15, :], axis=1), np.linalg.norm(d_tc[:, 16, :], axis=1))
    n_speed = np.linalg.norm(d_tc[:, 0, :], axis=1)
    d_elb = np.abs(np.diff(avg_elb))
    d_wy_mid = np.diff(w_mid_y)
    slam_3step = w_mid_y[3:] - w_mid_y[:-3]  # 양수 = 3스텝 동안 급격한 양손 하강(샷건!)

    peaks_elb, _ = find_peaks(avg_elb, prominence=12.0, height=95.0)
    valleys_elb, _ = find_peaks(-avg_elb, prominence=12.0)
    d_elb_signed = np.diff(avg_elb)
    elb_reversals = int(np.sum((d_elb_signed[:-1] * d_elb_signed[1:]) < -4.0))
    wy_reversals = int(np.sum((d_wy_mid[:-1] * d_wy_mid[1:]) < -0.002))

    sk_w = np.max(tc[:, :, 0], axis=1) - np.min(tc[:, :, 0], axis=1)
    sk_h = np.max(tc[:, :, 1], axis=1) - np.min(tc[:, :, 1], axis=1)
    wh_ratio = sk_w / np.maximum(sk_h, 0.2)

    s_feats = [
        # 1. 팔꿈치 각도 및 진동 (12)
        float(np.min(avg_elb)), float(np.max(avg_elb)), float(np.mean(avg_elb)), float(np.std(avg_elb)),
        float(np.min(min_elb_lr)), float(np.max(max_elb_lr)),
        float(np.sum(d_elb)), float(np.max(d_elb)),
        float(len(peaks_elb)), float(len(valleys_elb)), float(elb_reversals),
        float(np.sum(avg_elb > 110.0)),

        # 2. 무릎 각도 및 하체 비대칭 (14)
        float(np.min(min_knee_lr)), float(np.percentile(min_knee_lr, 10)), float(np.mean(avg_knee)), float(np.std(avg_knee)),
        float(np.median(max_knee_lr)), float(np.max(max_knee_lr - min_knee_lr)),
        float(np.min(ank_mid_y)), float(np.max(ank_mid_y)), float(np.mean(ank_mid_y)), float(np.ptp(ank_mid_y)),
        float(np.min(np.minimum(l_ay, r_ay))), float(np.max(ank_asym_y)), float(np.max(ank_sep_2d)), float(np.max(knee_asym_y)),

        # 3. 손목 궤적, 양손 대칭성, 머리 위 타격 충격량 (24)
        float(np.min(w_mid_y)), float(np.max(w_mid_y)), float(np.mean(w_mid_y)), float(np.ptp(w_mid_y)),
        float(np.ptp(w_mid_x)), float(np.std(w_mid_x)),
        act_wy_span, sec_wy_span, two_hand_y_ratio,
        act_wx_span, sec_wx_span, two_hand_x_ratio,
        wy_corr, wx_corr,
        float(np.min(np.minimum(l_wy_sh, r_wy_sh))), float(np.min(np.maximum(l_wy_sh, r_wy_sh))),
        float(np.max(np.maximum(l_wy, r_wy))), float(np.min(w_dist)), float(np.max(w_dist)),
        float(wy_reversals),
        float(np.min(w_rel_nose_y)), float(np.min(min_single_w_rel_nose)),
        float(np.max(slam_3step)), float(np.min(slam_3step)),

        # 4. 척추 기울기, 좌우 롤 비대칭, 종횡비 및 관절 속도 (26)
        float(np.min(nose_y)), float(np.max(nose_y)), float(np.mean(nose_y)), float(np.ptp(nose_y)),
        float(np.ptp(nose_x)), float(np.max(nose_rel_sh_y)),
        float(np.max(sp_tilt)), float(np.percentile(sp_tilt, 85)), float(np.mean(sp_tilt)),
        float(np.max(wh_ratio)), float(np.percentile(wh_ratio, 85)), float(np.min(wh_ratio)),
        float(np.max(w_speed)), float(np.mean(w_speed)),
        float(np.max(a_speed)), float(np.mean(a_speed)),
        float(np.max(n_speed)), float(np.max(d_wy_mid)),
        float(np.max(sh_roll_y)), float(np.mean(sh_roll_y)),
        float(np.max(hip_roll_y)), float(np.mean(hip_roll_y)),
        float(np.max(ear_roll_y)), float(np.min(sh_width_tc)),
        float(np.max(sh_width_tc)), float(np.ptp(sh_x)),

        # 5. 시작-중반-종료 자세 변화량 (10)
        float(np.mean(nose_y[10:20]) - np.mean(nose_y[:5])),
        float(np.mean(nose_y[24:]) - np.mean(nose_y[:5])),
        float(np.mean(ank_mid_y[10:20]) - np.mean(ank_mid_y[:5])),
        float(np.mean(ank_mid_y[24:]) - np.mean(ank_mid_y[:5])),
        float(np.mean(avg_knee[10:20]) - np.mean(avg_knee[:5])),
        float(np.mean(avg_knee[24:]) - np.mean(avg_knee[:5])),
        float(np.mean(sp_tilt[10:20]) - np.mean(sp_tilt[:5])),
        float(np.mean(sp_tilt[24:]) - np.mean(sp_tilt[:5])),
        float(np.mean(knee_mid_y[10:20])),
        float(np.max(np.abs(sh_x))),
    ]
    return np.nan_to_num(np.asarray(s_feats, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)


def predict_person(p: dict, m: dict) -> dict:
    info = m["info"]
    steps, rule = info["sequence_steps"], info["valid_frame_rule"]
    t, x, ok = np.asarray(p["t"]), np.stack(p["x"]), np.asarray(p["ok"])
    span = float(t[-1] - t[0]) if len(t) > 1 else 0.0           # 이 사람이 화면에 보인 시간(초)
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
        probs.append(predict_proba(seq, m))                    # 창 하나 → 17동작 확률
    if not probs:
        return {"skipped": True, "reason": "관절이 잘 보이는 Frame 이 부족함", **quality}

    mean_p = np.mean(probs, axis=0)                            # 창이 여러 개면 평균 → 1등·top3
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
    for tid in sorted(people):                                 # 사람마다 따로 판단, 못 한 사람은 skipped 로
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
