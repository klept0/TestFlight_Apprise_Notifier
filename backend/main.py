import json
import asyncio
import aiohttp
import uvicorn
import apprise
import threading
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pathlib import Path

# Constants
DATA_FILE = Path("testflight_ids.json")
TESTFLIGHT_URL = "https://testflight.apple.com/join/"
SLEEP_TIME = 10  # in seconds
APPRISE_URLS = ["mailto://user:password@smtp.example.com"]
active_connections = set()

# Initialize Apprise notifier
apobj = apprise.Apprise()
for url in APPRISE_URLS:
    apobj.add(url)

# FastAPI server
app = FastAPI()

# Load & Save Functions
def load_ids():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f).get("testflight_ids", [])
    return []

def save_ids(ids):
    with open(DATA_FILE, "w") as f:
        json.dump({"testflight_ids": ids}, f, indent=4)

# WebSocket Manager
async def broadcast(message):
    """Send a message to all connected WebSocket clients."""
    for connection in active_connections:
        await connection.send_text(message)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)

# API Endpoints
@app.get("/api/testflight-ids")
def get_testflight_ids():
    return JSONResponse(content={"testflight_ids": load_ids()})

@app.post("/api/add-id/{testflight_id}")
async def add_testflight_id(testflight_id: str):
    ids = load_ids()
    if testflight_id in ids:
        raise HTTPException(status_code=400, detail="ID already exists")
    ids.append(testflight_id)
    save_ids(ids)
    await broadcast(json.dumps({"type": "update", "testflight_ids": ids}))
    return JSONResponse(content={"message": "ID added", "testflight_ids": ids})

@app.delete("/api/remove-id/{testflight_id}")
async def remove_testflight_id(testflight_id: str):
    ids = load_ids()
    if testflight_id not in ids:
        raise HTTPException(status_code=404, detail="ID not found")
    ids.remove(testflight_id)
    save_ids(ids)
    await broadcast(json.dumps({"type": "update", "testflight_ids": ids}))
    return JSONResponse(content={"message": "ID removed", "testflight_ids": ids})

# Start FastAPI
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8089)
