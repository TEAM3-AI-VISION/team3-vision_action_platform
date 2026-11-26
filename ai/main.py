from fastapi import FastAPI
import os, shutil, tempfile
from fastapi import UploadFile, File, HTTPException
from inference import predict

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
        return predict(tmp.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        os.remove(tmp.name)