from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = []

# Command costs in channel points
COMMAND_COSTS = {
    1: 100,   # Feed my fish
    2: 100,   # Feed a fish
    3: 50,    # Clean tank
    4: 100,   # Progress tank
    5: 250,   # Feed all fish
    6: 10000, # Spawn fish
    7: 500    # Increase max health
}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    print("✓ Godot client connected via WebSocket")
    try:
        while True:
            data = await ws.receive_text()
            print(f"Received from Godot: {data}")
    except WebSocketDisconnect:
        print("Client disconnected normally")
    except Exception as e:
        print(f"Client disconnected with error: {e}")
    finally:
        if ws in connected_clients:
            connected_clients.remove(ws)
#Later points will come from Twitch API/authentication
@app.post("/button/{button_id}")
async def button_pressed(button_id: int, user_points: int = 999999):
    """
    user_points will come from Twitch later
    For now, defaulting to 999999 for testing
    """
    cost = COMMAND_COSTS.get(button_id, 0)
#Returns error response if insufficient (doesn't send to Godot)
    if user_points < cost:
        return {"status": "insufficient_points", "required": cost, "has": user_points}
    
    print(f"✓ Button {button_id} pressed (Cost: {cost}g)")
    
    # Forward to all connected Godot clients
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_text(f"button:{button_id}")
            print(f"  → Sent to Godot client")
        except:
            disconnected.append(client)
    
    for client in disconnected:
        connected_clients.remove(client)
    
    return {
        "status": "sent", 
        "button": button_id, 
        "cost": cost,
        "clients": len(connected_clients)
    }

@app.get("/")
async def root():
    return {"status": "server running", "connected_clients": len(connected_clients)}

@app.get("/commands")
async def get_commands():
    """Return available commands and their costs"""
    return {
        "commands": [
            {"id": 1, "name": "Feed My Fish", "cost": 100},
            {"id": 2, "name": "Feed A Fish", "cost": 100},
            {"id": 3, "name": "Clean Tank", "cost": 50},
            {"id": 4, "name": "Progress Tank", "cost": 100},
            {"id": 5, "name": "Feed All Fish", "cost": 250},
            {"id": 6, "name": "Spawn Fish", "cost": 10000},
            {"id": 7, "name": "Increase Max Health", "cost": 500}
        ]
    }

app.mount("/panel", StaticFiles(directory="../FishCornerCommandExtension", html=True), name="panel")

if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    print("📡 WebSocket: ws://127.0.0.1:8000/ws")
    print("🌐 Panel: http://127.0.0.1:8000/panel/index.html")
    print("🔌 Commands: http://127.0.0.1:8000/commands")
    uvicorn.run(app, host="127.0.0.1", port=8000)