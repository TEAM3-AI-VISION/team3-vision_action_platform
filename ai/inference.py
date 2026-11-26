"""무인 상점 이상행동 17동작 추론: MP4 한 개 → 사람별 동작·확률

1. Frame 마다 사람별 관절 17개 (YOLO Pose, 사람 번호는 ByteTrack)
2. 관절 → 값 40개
3. 8초씩 잘라 30칸으로 맞춤 → 값 2,646개
4. ExtraTrees 확률과 1D-CNN 확률의 평균 → 17동작 확률
5. 사람별 1등·top3

값을 만드는 계산과 기준 숫자는 학습 때와 똑같아야 한다. 하나라도 다르면 모델이 엉뚱한 답을 낸다.
실행: python inference.py video.mp4
"""
import json
import sys
import time
from functools import lru_cache
from pathlib import Path

import cv2
import joblib
import numpy as np
import torch
from scipy.signal import find_peaks
from torch import nn
from ultralytics import YOLO

MODEL_DIR = Path(__file__).parent / "models"
STEPS = 30          # 창 하나를 30칸으로
WINDOW = 8.0        # 창 길이(초). 학습 영상 중간값 5.4초 → 동작 하나가 들어간다
STRIDE = 4.0        # 긴 영상은 4초씩 밀며 여러 창
UNCERTAIN = 0.3     # 1등 확률이 이보다 낮으면 불확실
PROCESS_FPS = 15    # 1초에 15장만 관절을 찾는다. 30장과 1등이 같고(영상 84개) 시간은 절반
ACTIONS = {
    "A001": "키오스크 두드리기", "A002": "한손 물건 던지기", "A003": "앞차기", "A004": "잡고 흔들기",
    "A005": "밀치기", "A006": "휘두르기", "A007": "바닥에 앉기", "A008": "위에 앉기", "A009": "앞으로 눕기",
    "A010": "뒤로 눕기", "A011": "몸통 박치기", "A012": "쪼그려 물건 빼내기", "A013": "샷건", "A014": "구토",
    "A015": "옆으로 쓰러지기", "A016": "뒤로 넘어지기", "A017": "앞으로 넘어지기",
}


class CNN(nn.Module):
    """1D-CNN: (값 40 + 변화량 40, 30칸) → 17동작. 층 순서가 저장된 가중치와 같아야 한다."""

    def __init__(self, c_in, n_cls, w):
        super().__init__()

        def block(a, b, k):
            return nn.Sequential(nn.Conv1d(a, b, k, padding=k // 2), nn.BatchNorm1d(b), nn.ReLU())
        self.f = nn.Sequential(block(c_in, w, 5), block(w, w, 5), nn.Dropout(0.3), block(w, 2 * w, 3))
        self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(4 * w, n_cls))

    def forward(self, x):
        h = self.f(x)
        return self.head(torch.cat([h.mean(2), h.amax(2)], 1))


@lru_cache(maxsize=1)
def load_models():
    """처음 요청 때 한 번만 읽고, 다음 요청부터는 다시 씀"""
    ckpt = torch.load(MODEL_DIR / "action_cnn.pt", map_location="cpu", weights_only=True)
    cnns = []
    for state in ckpt["state_dicts"]:               # 난수만 바꿔 3번 학습한 CNN → 확률 평균
        net = CNN(2 * ckpt["n_frame"], ckpt["n_cls"], ckpt["width"])
        net.load_state_dict(state)
        cnns.append(net.eval())
    trees = joblib.load(MODEL_DIR / "action_model.joblib")
    return {"pose": YOLO(str(MODEL_DIR / "yolo11m-pose.pt")), "trees": trees, "cnns": cnns,
            "mu": ckpt["mu"].numpy(), "sd": ckpt["sd"].numpy(),       # CNN 입력 크기를 맞출 학습 때 평균·표준편차
            "ids": [str(c) for c in trees.classes_]}                  # 확률 순서 = A001 ~ A017


# ---------------------------------------------------------------- 1·2. 영상 → 사람별 값 40개
def frame_values(kp, w, h):
    """관절 17개 (x, y px) → 값 40개, 쓸 수 있는 Frame 인지
    34 = 골반 중심 기준 좌표 ÷ 몸 크기 (카메라와 멀든 가깝든 같은 값)
     4 = 팔꿈치·무릎 각도
     2 = 골반의 화면 위치 0~1 (넘어지면 아래로)
    """
    hip, shoulder = (kp[11] + kp[12]) / 2.0, (kp[5] + kp[6]) / 2.0
    shoulder_w = float(np.linalg.norm(kp[5] - kp[6]))
    # 몸 크기 = 몸통 길이와 (어깨 폭 ÷ 0.70) 중 큰 쪽. 옆모습(어깨 폭↓)·누운 자세(몸통↓)에서도 0 이 안 되게
    size = max(float(np.linalg.norm(hip - shoulder)), shoulder_w / 0.70)
    if size < 5.0 or shoulder_w < 1e-8:                          # 5px 도 안 되면 너무 작거나 잘못 찾은 사람
        return np.full(40, np.nan), False
    joint = kp[[7, 8, 13, 14]]                                   # 꼭짓점: 팔꿈치·무릎
    ba, bc = kp[[5, 6, 11, 12]] - joint, kp[[9, 10, 15, 16]] - joint   # → 어깨·골반, → 손목·발목
    with np.errstate(invalid="ignore", divide="ignore"):         # 관절이 겹치면 각도 없음(nan)
        cos = (ba * bc).sum(1) / (np.linalg.norm(ba, axis=1) * np.linalg.norm(bc, axis=1))
    angles = np.degrees(np.arccos(np.clip(cos.astype(np.float64), -1.0, 1.0)))
    values = np.r_[np.round(((kp - hip) / size).reshape(-1), 6), np.round(angles, 6),   # 학습 데이터와 같은 자릿수
                   round(float(hip[0]) / w, 4), round(float(hip[1]) / h, 4)]
    return values, bool(np.isfinite(values).all())


def track_people(path, models):
    """영상 → {사람 번호: [(시간, 값 40개, 쓸 수 있나, 관절 Confidence 평균), ...]}"""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("영상 파일을 열 수 없습니다.")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 0 < fps <= 240:                          # fps 정보가 없거나 이상하면 30 으로
        fps = 30.0
    step = max(1, round(fps / PROCESS_FPS))         # 30fps 영상은 2장 중 1장, 60fps 는 4장 중 1장
    people, n = {}, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        if (n - 1) % step:
            continue
        # imgsz·conf 는 학습 데이터를 만들 때와 같은 값. persist: 앞 Frame 의 사람 번호를 이어 받는다
        result = models["pose"].track(frame, persist=n > 1, tracker="bytetrack.yaml", imgsz=640, conf=0.25,
                                      verbose=False)[0]
        if result.boxes.id is None:
            continue                                # 이 Frame 에는 사람이 없음
        for tid, kp, conf in zip(result.boxes.id.int().tolist(), result.keypoints.xy.cpu().numpy(),
                                 result.keypoints.conf.cpu().numpy()):
            values, usable = frame_values(kp, frame.shape[1], frame.shape[0])
            people.setdefault(tid, []).append(((n - 1) / fps, values, usable, float(conf.mean())))
    cap.release()
    if n == 0:
        raise ValueError("영상에서 Frame 을 읽지 못했습니다.")
    return people


# ---------------------------------------------------------------- 3·4. 값 → 17동작 확률
def motion_summary(seq):
    """30칸 × 38 (좌표 34 + 각도 4) → 움직임 요약 86개
    두 손이 같이 움직이는지, 팔을 몇 번 폈다 굽혔는지, 손이 코 위로 갔는지, 몸이 기우는지 같은 것.
    """
    seq = np.asarray(seq, dtype=np.float32)
    body = seq[:, :34].reshape(30, 17, 2)
    body = body / np.maximum(np.linalg.norm((body[:, 5] + body[:, 6]) / 2.0, axis=1), 0.40)[:, None, None]  # 몸 크기로 한 번 더
    x, y = body[..., 0], body[..., 1]                           # (30칸, 관절 17). 관절 번호는 COCO

    def mid(v, a, b): return (v[:, a] + v[:, b]) / 2.0          # 두 관절의 가운데
    def dy(a, b): return np.abs(y[:, a] - y[:, b])              # 두 관절의 높이 차
    def dist(a, b): return np.hypot(x[:, a] - x[:, b], y[:, a] - y[:, b])
    def speed(j): return np.linalg.norm(np.diff(body[:, j], axis=0), axis=1)
    def stat4(v): return [v.min(), v.max(), v.mean(), np.ptp(v)]
    def turns(dv, th): return int(np.sum(dv[:-1] * dv[1:] < -th))    # 방향이 바뀐 횟수
    def change(v): return [np.mean(v[10:20]) - np.mean(v[:5]), np.mean(v[24:]) - np.mean(v[:5])]  # 처음 → 중간·끝

    def hands(l, r):                                            # 더 움직인 손, 덜 움직인 손, 비율
        a, b = max(float(np.ptp(l)), float(np.ptp(r))), min(float(np.ptp(l)), float(np.ptp(r)))
        return [a, b, b / max(a, 1e-4)]

    def corr(a, b):                                             # 두 손이 같이 움직이는 정도
        return float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 1e-3 and np.std(b) > 1e-3 else 0.0

    elbow, knee = mid(seq, 34, 35), mid(seq, 36, 37)            # 팔꿈치·무릎 각도 (좌우 평균)
    knee_lo, knee_hi = np.minimum(seq[:, 36], seq[:, 37]), np.maximum(seq[:, 36], seq[:, 37])
    sh_x, sh_y = mid(x, 5, 6), mid(y, 5, 6)                     # 어깨 가운데
    hand_x, hand_y = mid(x, 9, 10), mid(y, 9, 10)               # 두 손목 가운데
    lx, ly, rx, ry = x[:, 9], y[:, 9], x[:, 10], y[:, 10]       # 왼손목·오른손목
    nose_y, ankle_y = y[:, 0], mid(y, 15, 16)
    tilt = np.degrees(np.arctan2(np.abs(sh_x), np.maximum(-sh_y, 1e-3)))    # 상체 기울기
    wide = np.ptp(x, 1) / np.maximum(np.ptp(y, 1), 0.2)         # 몸 가로 ÷ 세로 (누우면 커짐)
    hand_v, foot_v = np.maximum(speed(9), speed(10)), np.maximum(speed(15), speed(16))
    d_elbow, d_hand_y = np.diff(elbow), np.diff(hand_y)
    slam = hand_y[3:] - hand_y[:-3]                             # 3칸 동안 두 손이 확 내려감 (샷건)

    # 아래 기준 숫자(12, 95, 110, 4, 0.002 …)는 학습 때 정한 값 그대로
    feats = [
        # 팔꿈치 (12)
        elbow.min(), elbow.max(), elbow.mean(), elbow.std(),
        np.minimum(seq[:, 34], seq[:, 35]).min(), np.maximum(seq[:, 34], seq[:, 35]).max(),
        np.abs(d_elbow).sum(), np.abs(d_elbow).max(),
        len(find_peaks(elbow, prominence=12.0, height=95.0)[0]), len(find_peaks(-elbow, prominence=12.0)[0]),
        turns(d_elbow, 4.0), np.sum(elbow > 110.0),
        # 무릎·발목 (14)
        knee_lo.min(), np.percentile(knee_lo, 10), knee.mean(), knee.std(), np.median(knee_hi), (knee_hi - knee_lo).max(),
        *stat4(ankle_y), np.minimum(y[:, 15], y[:, 16]).min(), dy(15, 16).max(), dist(15, 16).max(), dy(13, 14).max(),
        # 손목 (24)
        *stat4(hand_y), np.ptp(hand_x), hand_x.std(), *hands(ly, ry), *hands(lx, rx), corr(ly, ry), corr(lx, rx),
        np.minimum(ly - sh_y, ry - sh_y).min(), np.maximum(ly - sh_y, ry - sh_y).min(), np.maximum(ly, ry).max(),
        dist(9, 10).min(), dist(9, 10).max(), turns(d_hand_y, 0.002),
        (hand_y - nose_y).min(), (np.minimum(ly, ry) - nose_y).min(), slam.max(), slam.min(),
        # 머리·상체 기울기·몸 비율·속도 (26)
        *stat4(nose_y), np.ptp(x[:, 0]), (nose_y - sh_y).max(), tilt.max(), np.percentile(tilt, 85), tilt.mean(),
        wide.max(), np.percentile(wide, 85), wide.min(), hand_v.max(), hand_v.mean(), foot_v.max(), foot_v.mean(),
        speed(0).max(), d_hand_y.max(), dy(5, 6).max(), dy(5, 6).mean(), dy(11, 12).max(), dy(11, 12).mean(),
        dy(3, 4).max(), dist(5, 6).min(), dist(5, 6).max(), np.ptp(sh_x),
        # 처음 → 중간·끝 자세 변화 (10)
        *change(nose_y), *change(ankle_y), *change(knee), *change(tilt), np.mean(mid(y, 13, 14)[10:20]), np.abs(sh_x).max(),
    ]
    return np.nan_to_num(np.asarray(feats, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)


def predict_proba(seq, models):
    """30칸 × 40 → 17동작 확률"""
    seq = seq[None]                                             # (1, 30, 40)
    lo, hi = seq.min(1), seq.max(1)
    # ExtraTrees 입력 2,646 = 값 1,200 + 변화량 1,160 + 평균·표준편차·최소·최대·범위 200 + 움직임 요약 86
    features = np.hstack([seq.reshape(1, -1), np.diff(seq, axis=1).reshape(1, -1), seq.mean(1), seq.std(1),
                          lo, hi, hi - lo, motion_summary(seq[0, :, :38])[None]])
    p_trees = models["trees"].predict_proba(features)
    # 1D-CNN 입력: 값 40 + 변화량 40 채널, 30칸
    cnn_in = np.concatenate([seq, np.diff(seq, axis=1, prepend=seq[:, :1])], 2).transpose(0, 2, 1).astype(np.float32)
    with torch.no_grad():
        x = torch.tensor((cnn_in - models["mu"]) / models["sd"])
        p_cnn = np.mean([torch.softmax(net(x), 1).numpy() for net in models["cnns"]], 0)
    return ((p_trees + p_cnn) / 2)[0]


def predict_person(track, models):
    """한 사람 → 17동작 확률 (판단할 수 없으면 None)"""
    times, values, usable, conf = map(np.array, zip(*track))
    if times[-1] - times[0] < 1.0 or round(conf.mean(), 3) < 0.4:   # 1초도 안 보였거나, 관절이 흐릿함(의자·가방)
        return None
    # 8초 이하면 창 하나, 길면 4초씩 밀며 여러 창 (마지막 창은 끝에 맞춤)
    if times[-1] - times[0] <= WINDOW:
        starts = [times[0]]
    else:
        starts = [*np.arange(times[0], times[-1] - WINDOW, STRIDE), times[-1] - WINDOW]
    probs = []
    for start in starts:
        inside = (times >= start - 1e-9) & (times <= start + WINDOW + 1e-9)   # 1e-9: 소수점 오차
        good = inside & usable
        if good.sum() < 10 or good.sum() / inside.sum() < 0.5:   # 쓸 수 있는 Frame 이 10장 미만·절반 미만이면 버림
            continue
        t, v = times[good], values[good]
        pos = (t - t[0]) / (t[-1] - t[0])                       # 창 안의 위치 0~1 → 30칸으로 고르게
        seq = np.stack([np.interp(np.linspace(0.0, 1.0, STEPS), pos, v[:, j]) for j in range(40)], 1)
        probs.append(predict_proba(seq, models))
    return np.mean(probs, 0) if probs else None                # 창이 여러 개면 평균


# ---------------------------------------------------------------- 5. 영상 하나 → 결과
def predict_video(path):
    started = time.perf_counter()
    models = load_models()
    people = track_people(path, models)
    predictions = []
    for tid, track in sorted(people.items()):                  # 사람마다 따로 판단
        prob = predict_person(track, models)
        if prob is None:
            continue
        top3 = [{"action_id": models["ids"][i], "action_name_ko": ACTIONS[models["ids"][i]],
                 "probability": round(float(prob[i]), 4)} for i in np.argsort(prob)[::-1][:3]]
        best = top3[0]
        predictions.append({"track_id": tid, "action_id": best["action_id"], "action_name_ko": best["action_name_ko"],
                            "confidence": best["probability"], "uncertain": best["probability"] < UNCERTAIN,
                            "top3": top3})
    message = ""
    if not predictions:
        message = "사람은 보였지만 동작을 판단하지 못했습니다." if people else "영상에서 사람을 찾지 못했습니다."
    return {"message": message, "processing_seconds": round(time.perf_counter() - started, 2),   # 7초 영상이 GPU 약 3초, CPU 약 16초
            "predictions": predictions}


if __name__ == "__main__":
    print(json.dumps(predict_video(sys.argv[1]), ensure_ascii=False, indent=2))
