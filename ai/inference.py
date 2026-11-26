"""무인 상점 이상행동 17동작 추론: MP4 한 개 → 사람별 동작·확률

1. 영상의 Frame 마다 사람별 관절 17개 찾기 (YOLO Pose, 사람 번호는 ByteTrack)
2. 관절 → 값 40개
3. 8초씩 잘라 30칸으로 맞춤 → 값 2,646개
4. ExtraTrees 확률과 1D-CNN 확률의 평균 → 17동작 확률
5. 사람별 1등·top3

값을 만드는 계산은 학습 때와 똑같아야 한다. 하나라도 다르면 모델이 엉뚱한 답을 낸다.
실행: python inference.py video.mp4
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import joblib
import numpy as np
import torch
from scipy.signal import find_peaks
from torch import nn
from ultralytics import YOLO

MODELS = Path(__file__).parent / "models"
STEPS = 30          # 창 하나를 30칸으로
WINDOW = 8.0        # 창 길이(초). 학습 영상 중간값 5.4초 → 동작 하나가 들어간다
STRIDE = 4.0        # 긴 영상은 4초씩 밀며 여러 창
UNCERTAIN = 0.3     # 1등 확률이 이보다 낮으면 불확실


# ---------------------------------------------------------------- 모델 불러오기
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
    d = torch.load(MODELS / "action_cnn.pt", map_location="cpu", weights_only=True)
    cnns = []
    for state in d["state_dicts"]:                  # 난수만 바꿔 3번 학습한 CNN → 확률 평균
        net = CNN(2 * d["n_frame"], d["n_cls"], d["width"])
        net.load_state_dict(state)
        cnns.append(net.eval())
    info = json.loads((MODELS / "training_info.json").read_text(encoding="utf-8"))
    trees = joblib.load(MODELS / "action_model.joblib")
    return {"pose": YOLO(str(MODELS / "yolo11m-pose.pt")), "trees": trees, "cnns": cnns,
            "mu": d["mu"].numpy(), "sd": d["sd"].numpy(),
            "classes": [str(c) for c in trees.classes_],
            "names": {k: v["name_ko"] for k, v in info["class_info"].items()}}


# ---------------------------------------------------------------- 1·2. 영상 → 사람별 값 40개
def frame_values(kp, w, h):
    """관절 17개 (x, y px) → 값 40개, 쓸 수 있는 Frame 인지
    34 = 골반 중심 기준 좌표 ÷ 몸 크기 (카메라와 멀든 가깝든 같은 값)
     4 = 팔꿈치·무릎 각도
     2 = 골반의 화면 위치 0~1 (넘어지면 아래로)
    """
    hip, sh = (kp[11] + kp[12]) / 2.0, (kp[5] + kp[6]) / 2.0
    sh_w = float(np.linalg.norm(kp[5] - kp[6]))
    size = max(float(np.linalg.norm(hip - sh)), sh_w / 0.70)     # 옆모습·누운 자세에서도 0 이 안 되게
    if size < 5.0 or sh_w < 1e-8:
        return np.full(40, np.nan), False
    mid = kp[[7, 8, 13, 14]]                                     # 꼭짓점: 팔꿈치·무릎
    ba, bc = kp[[5, 6, 11, 12]] - mid, kp[[9, 10, 15, 16]] - mid  # → 어깨·골반, → 손목·발목
    with np.errstate(invalid="ignore", divide="ignore"):         # 관절이 겹치면 각도 없음(nan)
        cos = (ba * bc).sum(1) / (np.linalg.norm(ba, axis=1) * np.linalg.norm(bc, axis=1))
    angles = np.degrees(np.arccos(np.clip(cos.astype(np.float64), -1.0, 1.0)))
    v = np.r_[np.round(((kp - hip) / size).reshape(-1), 6), np.round(angles, 6),
              round(float(hip[0]) / w, 4), round(float(hip[1]) / h, 4)]
    return v, bool(np.isfinite(v).all())


def track_people(path, m):
    """영상 → {사람 번호: [(시간, 값 40개, 쓸 수 있나, 관절 Confidence 평균), ...]}"""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("영상 파일을 열 수 없습니다.")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 0 < fps <= 240:
        fps = 30.0
    step = max(1, round(fps / 30))                  # 60fps 영상은 2장 중 1장만 → 처리 시간 절반
    people, n = {}, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        if (n - 1) % step:
            continue
        # persist: 첫 Frame 이 아니면 앞 Frame 의 사람 번호를 이어 받는다
        r = m["pose"].track(frame, persist=n > 1, tracker="bytetrack.yaml", imgsz=640, conf=0.25, verbose=False)[0]
        if r.boxes.id is None:
            continue                                # 이 Frame 에는 사람이 없음
        for tid, kp, conf in zip(r.boxes.id.int().tolist(), r.keypoints.xy.cpu().numpy(), r.keypoints.conf.cpu().numpy()):
            v, usable = frame_values(kp, frame.shape[1], frame.shape[0])
            people.setdefault(tid, []).append(((n - 1) / fps, v, usable, float(conf.mean())))
    cap.release()
    if n == 0:
        raise ValueError("영상에서 Frame 을 읽지 못했습니다.")
    return people


# ---------------------------------------------------------------- 3·4. 값 → 17동작 확률
def motion_summary(s):
    """30칸 × 38 (좌표 34 + 각도 4) → 움직임 요약 86개
    두 손이 같이 움직이는지, 팔을 몇 번 폈다 굽혔는지, 손이 코 위로 갔는지, 몸이 기우는지 같은 것.
    """
    f = np.asarray(s, dtype=np.float32)
    tc = f[:, :34].reshape(30, 17, 2)
    tc = tc / np.maximum(np.linalg.norm((tc[:, 5] + tc[:, 6]) / 2.0, axis=1), 0.40)[:, None, None]
    x, y = tc[..., 0], tc[..., 1]                               # (30칸, 관절 17)
    elb, knee = (f[:, 34] + f[:, 35]) / 2.0, (f[:, 36] + f[:, 37]) / 2.0
    knee_lo, knee_hi = np.minimum(f[:, 36], f[:, 37]), np.maximum(f[:, 36], f[:, 37])
    sx, sy = (x[:, 5] + x[:, 6]) / 2.0, (y[:, 5] + y[:, 6]) / 2.0   # 어깨 가운데
    tilt = np.degrees(np.arctan2(np.abs(sx), np.maximum(-sy, 1e-3)))  # 상체 기울기
    ny = y[:, 0]                                                # 코 높이
    lwx, lwy, rwx, rwy = x[:, 9], y[:, 9], x[:, 10], y[:, 10]   # 왼손목·오른손목
    wx, wy = (lwx + rwx) / 2.0, (lwy + rwy) / 2.0
    ay, ky = (y[:, 15] + y[:, 16]) / 2.0, (y[:, 13] + y[:, 14]) / 2.0   # 발목·무릎 가운데 높이
    d = np.diff(tc, axis=0)
    def speed(j): return np.linalg.norm(d[:, j], axis=1)
    hand_v, foot_v = np.maximum(speed(9), speed(10)), np.maximum(speed(15), speed(16))
    de, dwy = np.diff(elb), np.diff(wy)
    slam = wy[3:] - wy[:-3]                                     # 3칸 동안 두 손이 확 내려감 (샷건)
    wh = np.ptp(x, 1) / np.maximum(np.ptp(y, 1), 0.2)           # 몸 가로 ÷ 세로 (누우면 커짐)
    shw = np.hypot(x[:, 5] - x[:, 6], y[:, 5] - y[:, 6])        # 어깨 폭
    hand_gap = np.hypot(lwx - rwx, lwy - rwy)

    def stat4(v): return [v.min(), v.max(), v.mean(), np.ptp(v)]
    def turns(dv, th): return int(np.sum(dv[:-1] * dv[1:] < -th))    # 방향이 바뀐 횟수
    def change(v): return [np.mean(v[10:20]) - np.mean(v[:5]), np.mean(v[24:]) - np.mean(v[:5])]

    def hands(l, r):                                            # 더 움직인 손, 덜 움직인 손, 비율
        a, b = max(float(np.ptp(l)), float(np.ptp(r))), min(float(np.ptp(l)), float(np.ptp(r)))
        return [a, b, b / max(a, 1e-4)]

    def corr(a, b):                                             # 두 손이 같이 움직이는 정도
        return float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 1e-3 and np.std(b) > 1e-3 else 0.0

    feats = [
        # 팔꿈치 (12)
        elb.min(), elb.max(), elb.mean(), elb.std(),
        np.minimum(f[:, 34], f[:, 35]).min(), np.maximum(f[:, 34], f[:, 35]).max(),
        np.abs(de).sum(), np.abs(de).max(),
        len(find_peaks(elb, prominence=12.0, height=95.0)[0]), len(find_peaks(-elb, prominence=12.0)[0]),
        turns(de, 4.0), np.sum(elb > 110.0),
        # 무릎·발목 (14)
        knee_lo.min(), np.percentile(knee_lo, 10), knee.mean(), knee.std(), np.median(knee_hi), (knee_hi - knee_lo).max(),
        *stat4(ay), np.minimum(y[:, 15], y[:, 16]).min(), np.abs(y[:, 15] - y[:, 16]).max(),
        np.hypot(x[:, 15] - x[:, 16], y[:, 15] - y[:, 16]).max(), np.abs(y[:, 13] - y[:, 14]).max(),
        # 손목 (24)
        *stat4(wy), np.ptp(wx), wx.std(), *hands(lwy, rwy), *hands(lwx, rwx), corr(lwy, rwy), corr(lwx, rwx),
        np.minimum(lwy - sy, rwy - sy).min(), np.maximum(lwy - sy, rwy - sy).min(), np.maximum(lwy, rwy).max(),
        hand_gap.min(), hand_gap.max(), turns(dwy, 0.002),
        (wy - ny).min(), (np.minimum(lwy, rwy) - ny).min(), slam.max(), slam.min(),
        # 머리·상체 기울기·몸 비율·속도 (26)
        *stat4(ny), np.ptp(x[:, 0]), (ny - sy).max(), tilt.max(), np.percentile(tilt, 85), tilt.mean(),
        wh.max(), np.percentile(wh, 85), wh.min(), hand_v.max(), hand_v.mean(), foot_v.max(), foot_v.mean(),
        speed(0).max(), dwy.max(),
        np.abs(y[:, 5] - y[:, 6]).max(), np.abs(y[:, 5] - y[:, 6]).mean(),       # 어깨 좌우 높이 차
        np.abs(y[:, 11] - y[:, 12]).max(), np.abs(y[:, 11] - y[:, 12]).mean(),   # 골반 좌우 높이 차
        np.abs(y[:, 3] - y[:, 4]).max(), shw.min(), shw.max(), np.ptp(sx),
        # 처음 → 중간·끝 자세 변화 (10)
        *change(ny), *change(ay), *change(knee), *change(tilt), np.mean(ky[10:20]), np.abs(sx).max(),
    ]
    return np.nan_to_num(np.asarray(feats, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)


def predict_proba(s, m):
    """30칸 × 40 → 17동작 확률"""
    s = s[None]                                                 # (1, 30, 40)
    lo, hi = s.min(1), s.max(1)
    # ExtraTrees 입력 2,646 = 값 1,200 + 변화량 1,160 + 평균·표준편차·최소·최대·범위 200 + 움직임 요약 86
    x = np.hstack([s.reshape(1, -1), np.diff(s, axis=1).reshape(1, -1), s.mean(1), s.std(1), lo, hi, hi - lo,
                   motion_summary(s[0, :, :38])[None]])
    p_trees = m["trees"].predict_proba(x)
    # 1D-CNN 입력: 값 40 + 변화량 40 채널, 30칸. 학습 때 평균·표준편차로 크기를 맞춤
    c = np.concatenate([s, np.diff(s, axis=1, prepend=s[:, :1])], 2).transpose(0, 2, 1).astype(np.float32)
    with torch.no_grad():
        t = torch.tensor((c - m["mu"]) / m["sd"])
        p_cnn = np.mean([torch.softmax(net(t), 1).numpy() for net in m["cnns"]], 0)
    return ((p_trees + p_cnn) / 2)[0]


def predict_person(track, m):
    """한 사람 → 17동작 확률 (판단할 수 없으면 None)"""
    t, x, usable, conf = map(np.array, zip(*track))
    if t[-1] - t[0] < 1.0 or round(conf.mean(), 3) < 0.4:   # 1초도 안 보였거나, 관절이 흐릿함(의자·가방)
        return None
    if t[-1] - t[0] <= WINDOW:                                  # 8초 이하면 통째로 창 하나
        windows = [(t[0], t[-1])]
    else:                                                       # 길면 4초씩 밀며, 마지막 창은 끝에 맞춤
        starts = list(np.arange(t[0], t[-1] - WINDOW, STRIDE)) + [t[-1] - WINDOW]
        windows = [(float(a), float(a) + WINDOW) for a in starts]
    probs = []
    for a, b in windows:
        inside = (t >= a - 1e-9) & (t <= b + 1e-9)
        sel = inside & usable
        if sel.sum() < 10 or sel.sum() / inside.sum() < 0.5:   # 쓸 수 있는 Frame 이 적은 창은 버림
            continue
        ts, xs = t[sel], x[sel]
        u = (ts - ts[0]) / (ts[-1] - ts[0])                     # 창 길이와 상관없이 0~1 → 30칸
        seq = np.stack([np.interp(np.linspace(0.0, 1.0, STEPS), u, xs[:, j]) for j in range(40)], 1)
        probs.append(predict_proba(seq, m))
    return np.mean(probs, 0) if probs else None                # 창이 여러 개면 평균


# ---------------------------------------------------------------- 5. 영상 하나 → 결과
def predict_video(path):
    m = load_models()
    people = track_people(path, m)
    predictions = []
    for tid, track in sorted(people.items()):                  # 사람마다 따로 판단
        p = predict_person(track, m)
        if p is None:
            continue
        top3 = [{"action_id": m["classes"][i], "action_name_ko": m["names"][m["classes"][i]],
                 "probability": round(float(p[i]), 4)} for i in np.argsort(p)[::-1][:3]]
        best = top3[0]
        predictions.append({"track_id": tid, "action_id": best["action_id"], "action_name_ko": best["action_name_ko"],
                            "confidence": best["probability"], "uncertain": best["probability"] < UNCERTAIN,
                            "top3": top3})
    if not people:
        message = "영상에서 사람을 찾지 못했습니다."
    elif not predictions:
        message = "사람은 보였지만 동작을 판단하지 못했습니다."
    else:
        message = ""
    return {"message": message, "predictions": predictions}


if __name__ == "__main__":
    print(json.dumps(predict_video(sys.argv[1]), ensure_ascii=False, indent=2))
