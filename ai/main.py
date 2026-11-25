from fastapi import FastAPI

app = FastAPI(title="Team3 Vision Action AI")


@app.get("/health")
def health():
    return {"status": "ok"}
