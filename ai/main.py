from fastapi import FastAPI
import os, shutil, tempfile
from fastapi import UploadFile, File, HTTPException
# C1의 inference.py — 함수 이름이 아래 라우터(predict_video)와 같아서
# 모듈째 가져와 inference.predict_video() 로 부른다 (이름이 서로 덮어쓰지 않게)
import inference

app = FastAPI(title="Team3 Vision Action AI")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict/video")
def predict_video(video: UploadFile = File(...)):
    if not video.filename.lower().endswith(".mp4"):
        raise HTTPException(400, "MP4 파일만 가능합니다")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        shutil.copyfileobj(video.file, tmp)
    try:
        return inference.predict_video(tmp.name)   # 사람별 예측 결과를 그대로 반환
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        os.remove(tmp.name)