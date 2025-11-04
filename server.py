from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = []

COMMAND_COSTS = {
    1: 100,
    2: 100,
    3: 50,
    4: 100,
    5: 250,
    6: 10000,
    7: 500
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

@app.post("/button/{button_id}")
async def button_pressed(button_id: int, user_points: int = 999999):
    cost = COMMAND_COSTS.get(button_id, 0)
    
    if user_points < cost:
        return {"status": "insufficient_points", "required": cost, "has": user_points}
    
    print(f"✓ Button {button_id} pressed (Cost: {cost}g)")
    
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)