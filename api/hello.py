from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {"ok": True}

@app.get("/ping")
def ping():
    return {"pong": True}