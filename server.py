from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import uvicorn
import os
import hmac
import hashlib
import json

# Map
REWARD_ID_TO_BUTTON = {
    "08f530ad-0b8e-43fa-91da-05861241db81": 3,  # Clean Tank
    "21b5323c-18d6-4803-85eb-8ab6acf3a271": 2,  # Feed a Fish
    "301390c0-79da-45a1-a4fb-f15940565833": 4,  # Progress Tank
    "7d48717b-f8cf-42a3-ab07-f17111e07d63": 5,  # Feed All Fish
    "7f9b79f4-6492-4ae6-af0c-195c8da6670e": 7,  # Power Up My Fish
    "e785a1c1-4e7c-4b07-afc3-c7b717e23a41": 6,  # Spawn My Fish
    "f2d2e625-97a1-42db-8bb0-7ce385599ada": 8,  # Change My Fish (new)
    "f7a729bd-8c96-4d02-9239-df4af21621f2": 1,  # Feed My Fish
}


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = []
fish_registry = {}
current_fish_data = []  # Store current fish data from Godot

# Twitch EventSub secret (you'll set this when registering)
TWITCH_EVENTSUB_SECRET = os.environ.get("TWITCH_EVENTSUB_SECRET", "your_secret_here")

COMMAND_COSTS = {
    1: 100,    # Feed My Fish
    2: 100,    # Feed a Fish
    3: 50,     # Clean Tank
    4: 100,    # Progress Tank
    5: 250,    # Feed All Fish
    6: 2500,   # Spawn My Fish (updated)
    7: 500,    # Power Up My Fish
    8: 300     # Change My Fish (new)
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
    
    # Request initial fish data
    await ws.send_text("request:fish_list")
    
    try:
        while True:
            data = await ws.receive_text()
            print(f"Received from Godot: {data}")
            
            # Handle fish data updates from Godot
            if data.startswith("fish_data:"):
                fish_json = data.replace("fish_data:", "", 1)
                try:
                    global current_fish_data
                    current_fish_data = json.loads(fish_json)
                    print(f"Updated fish data: {len(current_fish_data)} fish")
                except json.JSONDecodeError:
                    print("Failed to parse fish data")
            
            elif data.startswith("fish_spawned:"):
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

@app.get("/fish")
async def get_fish():
    """Get available fish for feeding (excluding immortal starter fish)"""
    # Filter out starter fish and dead fish
    starter_names = ["Jay", "Kati", "Manu"]
    
    # If we have real fish data from Godot, use it
    if current_fish_data:
        available_fish = [
            fish for fish in current_fish_data 
            if fish.get("name") not in starter_names and fish.get("health", 0) > 0
        ]
        return {
            "success": True,
            "fish": available_fish,
            "count": len(available_fish)
        }
    
    # Otherwise return dummy data for testing
    dummy_fish = [
        {"index": 0, "name": "Bubbles", "species": "Goldfish", "health": 75, "max_health": 100},
        {"index": 1, "name": "Finn", "species": "Betta", "health": 50, "max_health": 100},
        {"index": 2, "name": "Coral", "species": "Clownfish", "health": 25, "max_health": 100},
        {"index": 3, "name": "Marina", "species": "Angelfish", "health": 90, "max_health": 100}
    ]
    
    return {
        "success": True,
        "fish": dummy_fish,
        "count": len(dummy_fish)
    }

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
    
    # Handle button 2 (Feed A Fish) with specific fish index
    if button_id == 2 and fish_index is not None:
        disconnected = []
        for client in connected_clients:
            try:
                await client.send_text(f"feed_fish:{fish_index}")
                print(f"  → Sent feed fish command for index {fish_index}")
            except:
                disconnected.append(client)
        
        for client in disconnected:
            connected_clients.remove(client)
            
        # Get fish name if available
        fish_name = "fish"
        if fish_index < len(current_fish_data):
            fish_name = current_fish_data[fish_index].get("name", "fish")
        
        return {
            "status": "sent",
            "button": button_id,
            "cost": cost,
            "username": username,
            "fish_name": fish_name,
            "fish_index": fish_index,
            "clients": len(connected_clients)
        }
    
    # Handle other buttons normally
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_text(f"button:{button_id}:user:{username}")
            print(f"  → Sent to Godot client")
        except:
            disconnected.append(client)
    
    for client in disconnected:
        connected_clients.remove(client)
    
    # Handle button 6 (Spawn Fish) - register in fish registry
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
    
    # Handle notification events
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
        
        elif event_type == "channel.channel_points_custom_reward_redemption.add":
            event_data = data["event"]
            reward_id = event_data["reward"]["id"]
            username = event_data["user_name"]
            user_id = event_data["user_id"]
            user_input = event_data.get("user_input", "")  # For "Feed a Fish" fish selection
            
            # Map reward to button command
            button_id = REWARD_ID_TO_BUTTON.get(reward_id)
            
            if not button_id:
                print(f"⚠️ Unknown reward redeemed: {reward_id}")
                return {"status": "unknown_reward"}
            
            print(f"✓ {username} redeemed reward (Button {button_id})")
            
            # Handle "Feed a Fish" - needs fish index from user input
            if button_id == 2:
                try:
                    fish_index = int(user_input)
                except:
                    print(f"⚠️ Invalid fish index: {user_input}")
                    return {"status": "invalid_input"}
                
                # Send feed command
                disconnected = []
                for client in connected_clients:
                    try:
                        await client.send_text(f"feed_fish:{fish_index}")
                    except:
                        disconnected.append(client)
                
                for client in disconnected:
                    connected_clients.remove(client)
            
            else:
                # Send regular button command
                disconnected = []
                for client in connected_clients:
                    try:
                        await client.send_text(f"button:{button_id}:user:{username}")
                    except:
                        disconnected.append(client)
                
                for client in disconnected:
                    connected_clients.remove(client)
            
            # Handle special cases
            if button_id == 6:  # Spawn My Fish
                if username not in fish_registry:
                    fish_registry[username] = []
                fish_registry[username].append(username)
            
            return {"status": "executed", "button": button_id, "username": username}
    
    return {"status": "ok"}

@app.get("/has-fish")
async def has_fish(username: str):
    has = username in fish_registry and len(fish_registry[username]) > 0
    return {"has_fish": has, "username": username}

@app.get("/fish-list")
async def get_fish_list():
    """Legacy endpoint - redirects to /fish"""
    return await get_fish()

@app.get("/")
async def root():
    return {
        "status": "server running",
        "connected_clients": len(connected_clients),
        "fish_count": len(current_fish_data)
    }

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