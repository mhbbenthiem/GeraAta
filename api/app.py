# /api/index.py  (SANITY TEST)
from fastapi import FastAPI
import os, sys
from pathlib import Path

app = FastAPI(title="sanity")

@app.get("/")
def root():
    return {
        "ok": True,
        "file": __file__,
        "cwd": os.getcwd(),
        "here": str(Path(__file__).resolve().parent),
        "sys_path_has_here": str(Path(__file__).resolve().parent) in sys.path,
        "routes": [r.path for r in app.routes],
    }

@app.get("/health")
def health():
    return {"status": "ok"}
