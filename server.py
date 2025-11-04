from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn
import os
import hmac
import hashlib

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = []
fish_registry = {}

# Twitch EventSub secret (you'll set this when registering)
TWITCH_EVENTSUB_SECRET = os.environ.get("TWITCH_EVENTSUB_SECRET", "your_secret_here")

COMMAND_COSTS = {
    1: 100,
    2: 100,
    3: 50,
    4: 100,
    5: 250,
    6: 10000,
    7: 500
}

SUBSCRIPTION_HP = {
    "1000": 100,  # Tier 1
    "2000": 150,  # Tier 2
    "3000": 200   # Tier 3
}

class ButtonRequest(BaseModel):
    user_points: int = 999999
    user_id: Optional[str] = None
    username: Optional[str] = None
    fish_index: Optional[int] = None

def verify_twitch_signature(request_body: bytes, signature: str, message_id: str, timestamp: str) -> bool:
    """Verify Twitch webhook signature"""
    hmac_message = message_id.encode() + timestamp.encode() + request_body
    expected_signature = "sha256=" + hmac.new(
        TWITCH_EVENTSUB_SECRET.encode(),
        hmac_message,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    print("✓ Godot client connected via WebSocket")
    try:
        while True:
            data = await ws.receive_text()
            print(f"Received from Godot: {data}")
            
            if data.startswith("fish_spawned:"):
                parts = data.split(":")
                if len(parts) >= 2:
                    fish_owner = parts[1]
                    if fish_owner not in fish_registry:
                        fish_registry[fish_owner] = []
                    fish_registry[fish_owner].append(fish_owner)
                    print(f"Registered fish for {fish_owner}")
                
    except WebSocketDisconnect:
        print("Client disconnected normally")
    except Exception as e:
        print(f"Client disconnected with error: {e}")
    finally:
        if ws in connected_clients:
            connected_clients.remove(ws)

@app.post("/button/{button_id}")
async def button_pressed(
    button_id: int, 
    request: Optional[ButtonRequest] = None,
    user_points: int = Query(999999)
):
    if request:
        points = request.user_points
        username = request.username or "unknown"
        user_id = request.user_id
        fish_index = request.fish_index
    else:
        points = user_points
        username = "unknown"
        user_id = None
        fish_index = None
    
    cost = COMMAND_COSTS.get(button_id, 0)
    
    if points < cost:
        return {"status": "insufficient_points", "required": cost, "has": points}
    
    print(f"✓ Button {button_id} pressed by {username} (Cost: {cost}g)")
    
    disconnected = []
    for client in connected_clients:
        try:
            if fish_index is not None:
                await client.send_text(f"button:{button_id}:user:{username}:fish:{fish_index}")
            else:
                await client.send_text(f"button:{button_id}:user:{username}")
            print(f"  → Sent to Godot client")
        except:
            disconnected.append(client)
    
    for client in disconnected:
        connected_clients.remove(client)
    
    if button_id == 6:
        if username not in fish_registry:
            fish_registry[username] = []
        fish_registry[username].append(username)
    
    return {
        "status": "sent", 
        "button": button_id, 
        "cost": cost,
        "username": username,
        "clients": len(connected_clients)
    }

@app.post("/eventsub")
async def eventsub_callback(
    request: Request,
    twitch_eventsub_message_signature: str = Header(None),
    twitch_eventsub_message_id: str = Header(None),
    twitch_eventsub_message_timestamp: str = Header(None),
    twitch_eventsub_message_type: str = Header(None)
):
    """Handle Twitch EventSub webhooks"""
    body = await request.body()
    
    # Verify signature
    if not verify_twitch_signature(
        body,
        twitch_eventsub_message_signature,
        twitch_eventsub_message_id,
        twitch_eventsub_message_timestamp
    ):
        print("❌ Invalid signature")
        return {"error": "Invalid signature"}, 403
    
    data = await request.json()
    
    # Handle verification challenge
    if twitch_eventsub_message_type == "webhook_callback_verification":
        print("✓ Webhook verification request received")
        return {"challenge": data["challenge"]}
    
    # Handle subscription event
    elif twitch_eventsub_message_type == "notification":
        event_type = data.get("subscription", {}).get("type")
        
        if event_type == "channel.subscribe":
            event_data = data["event"]
            username = event_data["user_name"]
            user_id = event_data["user_id"]
            tier = event_data["tier"]  # "1000", "2000", or "3000"
            
            hp = SUBSCRIPTION_HP.get(tier, 100)
            tier_name = {
                "1000": "Tier 1",
                "2000": "Tier 2",
                "3000": "Tier 3"
            }.get(tier, "Tier 1")
            
            print(f"✓ New subscription: {username} ({tier_name}) - {hp} HP fish")
            
            # Send to Godot
            disconnected = []
            for client in connected_clients:
                try:
                    await client.send_text(f"subscription:{username}:{tier}:{hp}")
                    print(f"  → Sent subscription to Godot")
                except:
                    disconnected.append(client)
            
            for client in disconnected:
                connected_clients.remove(client)
            
            # Register fish
            if username not in fish_registry:
                fish_registry[username] = []
            fish_registry[username].append(username)
    
    return {"status": "ok"}

@app.get("/has-fish")
async def has_fish(username: str):
    has = username in fish_registry and len(fish_registry[username]) > 0
    return {"has_fish": has, "username": username}

@app.get("/fish-list")
async def get_fish_list():
    for client in connected_clients:
        try:
            await client.send_text("request:fish_list")
        except:
            pass
    
    return {
        "fish": [
            {"name": "Jay", "species": "Betta"},
            {"name": "Kati", "species": "Betta"},
            {"name": "Manu", "species": "Betta"}
        ]
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