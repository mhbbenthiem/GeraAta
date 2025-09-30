# /api/hello.py
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "hint": "use /ping"}

@app.get("/ping")
def ping():
    return {"pong": True}