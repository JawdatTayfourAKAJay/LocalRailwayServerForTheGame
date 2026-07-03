from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict
import uvicorn
import os
import hmac
import hashlib
import json
from fastapi.responses import PlainTextResponse
import time
import requests

# ==================== TWITCH CREDENTIALS ====================
CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "giq1a0a7ncmdcl4xx9ilyfdoyyu1xr")
CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "yg4rardum7fsyaectewpx6aasculc2")
BROADCASTER_ID = os.environ.get("BROADCASTER_ID", "1259060048")
TWITCH_EVENTSUB_SECRET = os.environ.get("TWITCH_EVENTSUB_SECRET", "your_secret_here")
redemption_tracker: Dict[str, Dict[str, str]] = {}
twitch_oauth_token = None
token_expiry = 0

# ==================== GAME EVENT CONFIG ====================
GAME_API_KEY = os.environ.get("GAME_API_KEY", "your_secret_key_here")
ZAPIER_WEBHOOK_URL = os.environ.get("ZAPIER_WEBHOOK_URL", "https://hooks.zapier.com/hooks/catch/15270202/4bcxx16/")
HASH_SALT = os.environ.get("HASH_SALT", "your_secret_salt_here")

# ==================== REWARD MAP ====================
REWARD_ID_TO_BUTTON = {
    "08f530ad-0b8e-43fa-91da-05861241db81": 3,  # Clean Tank
    "21b5323c-18d6-4803-85eb-8ab6acf3a271": 2,  # Feed a Fish
    "301390c0-79da-45a1-a4fb-f15940565833": 4,  # Progress Tank
    "7d48717b-f8cf-42a3-ab07-f17111e07d63": 5,  # Feed All Fish
    "7f9b79f4-6492-4ae6-af0c-195c8da6670e": 7,  # Power Up My Fish
    "e785a1c1-4e7c-4b07-afc3-c7b717e23a41": 6,  # Spawn My Fish
    "f2d2e625-97a1-42db-8bb0-7ce385599ada": 8,  # Change My Fish
    "f7a729bd-8c96-4d02-9239-df4af21621f2": 1,  # Feed My Fish
}

# ==================== APP ====================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = []
fish_registry = {}
current_fish_data = []

COMMAND_COSTS = {
    1: 100,    # Feed My Fish
    2: 100,    # Feed a Fish
    3: 50,     # Clean Tank
    4: 100,    # Progress Tank
    5: 250,    # Feed All Fish
    6: 2500,   # Spawn My Fish
    7: 500,    # Power Up My Fish
    8: 300     # Change My Fish
}

SUBSCRIPTION_HP = {
    "1000": 100,  # Tier 1
    "2000": 150,  # Tier 2
    "3000": 200   # Tier 3
}

# ==================== MODELS ====================
class ButtonRequest(BaseModel):
    user_points: int = 999999
    user_id: Optional[str] = None
    username: Optional[str] = None
    fish_index: Optional[int] = None

class GameEvent(BaseModel):
    api_key: str
    steam_id: str
    achievement: str
    day: int
    # richer fields the game already sends
    schema_version: Optional[int] = None
    slot: Optional[str] = None
    event_name: Optional[str] = None
    timestamp_unix: Optional[int] = None
    # session fields
    session_start_unix: Optional[int] = None
    start_screen: Optional[str] = None
    total_active_playtime_seconds: Optional[float] = None
    total_active_playtime_minutes: Optional[float] = None
    # session_end fields
    sessions_count: Optional[int] = None
    quit_screen: Optional[str] = None
    quit_day: Optional[int] = None
    money_on_quit: Optional[float] = None
    end_fish_count: Optional[int] = None
    end_tank_count: Optional[int] = None
    end_unique_species_count: Optional[int] = None
    unique_species_seen_count: Optional[int] = None
    # milestone fields
    milestone_id: Optional[str] = None
    steam_achievement_id: Optional[str] = None
    # snapshot / extra — catch-all for merged dicts
    extra: Optional[Dict] = None

# ==================== HELPERS ====================
def hash_steam_id(steam_id: str) -> str:
    """Consistently hash a Steam ID with salt — same ID always gives same hash, irreversible."""
    salted = HASH_SALT + steam_id
    return hashlib.sha256(salted.encode()).hexdigest()

def get_twitch_oauth_token():
    """Get or refresh Twitch OAuth token"""
    global twitch_oauth_token, token_expiry
    current_time = time.time()
    if twitch_oauth_token and current_time < token_expiry:
        return twitch_oauth_token
    print("🔑 Getting new Twitch OAuth token...")
    response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials"
        }
    )
    if response.status_code == 200:
        data = response.json()
        twitch_oauth_token = data["access_token"]
        token_expiry = current_time + data.get("expires_in", 3600) - 3600
        print("✓ Got OAuth token")
        return twitch_oauth_token
    else:
        print(f"❌ Failed to get OAuth token: {response.status_code}")
        print(response.text)
        return None

async def refund_channel_points(username: str, reward_id: str, redemption_id: str):
    """Refund channel points by canceling the redemption"""
    token = get_twitch_oauth_token()
    if not token:
        print("❌ Cannot refund - no OAuth token")
        return False
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    url = "https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions"
    params = {
        "broadcaster_id": BROADCASTER_ID,
        "reward_id": reward_id,
        "id": redemption_id
    }
    payload = {"status": "CANCELED"}
    response = requests.patch(url, headers=headers, params=params, json=payload)
    if response.status_code == 200:
        print(f"✓ Refunded channel points for {username} (Reward: {reward_id})")
        return True
    else:
        print(f"❌ Failed to refund for {username}: {response.status_code}")
        print(response.text)
        return False

async def send_twitch_chat_message(message: str):
    """Send a message to Twitch chat"""
    token = get_twitch_oauth_token()
    if not token:
        print("❌ Cannot send chat message - no OAuth token")
        return False
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    url = "https://api.twitch.tv/helix/chat/messages"
    payload = {
        "broadcaster_id": BROADCASTER_ID,
        "sender_id": BROADCASTER_ID,
        "message": message
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print(f"✓ Sent chat message: {message}")
        return True
    else:
        print(f"❌ Failed to send chat message: {response.status_code}")
        print(response.text)
        return False

def verify_twitch_signature(request_body: bytes, signature: str, message_id: str, timestamp: str) -> bool:
    """Verify Twitch webhook signature"""
    hmac_message = message_id.encode() + timestamp.encode() + request_body
    expected_signature = "sha256=" + hmac.new(
        TWITCH_EVENTSUB_SECRET.encode(),
        hmac_message,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)

def forward_to_zapier(player_hash: str, event: GameEvent):
    payload = event.dict(exclude={"api_key", "steam_id"})
    payload["player_hash"] = player_hash
    try:
        response = requests.post(ZAPIER_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"[GameEvent] ✓ Forwarded to Zapier: {event.achievement} day {event.day}")
        else:
            print(f"[GameEvent] ❌ Zapier returned {response.status_code}")
    except Exception as e:
        print(f"[GameEvent] ❌ Zapier request failed: {e}")

# ==================== ROUTES ====================

@app.get("/")
async def root():
    return {
        "status": "server running",
        "connected_clients": len(connected_clients),
        "fish_count": len(current_fish_data)
    }

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    print("✓ Godot client connected via WebSocket")
    await ws.send_text("request:fish_list")
    try:
        while True:
            data = await ws.receive_text()
            print(f"Received from Godot: {data}")
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
            elif data.startswith("refund:"):
                parts = data.split(":")
                if len(parts) >= 3:
                    username = parts[1]
                    reward_id = parts[2]
                    if username in redemption_tracker and reward_id in redemption_tracker[username]:
                        redemption_id = redemption_tracker[username][reward_id]
                        success = await refund_channel_points(username, reward_id, redemption_id)
                        if success:
                            del redemption_tracker[username][reward_id]
                            print(f"✓ Refund processed for {username}")
                        else:
                            print(f"⚠️ Refund failed for {username}")
                    else:
                        print(f"⚠️ No redemption ID found for {username}, reward {reward_id}")
            elif data.startswith("chat_message:"):
                message = data.replace("chat_message:", "", 1)
                await send_twitch_chat_message(message)
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
    starter_names = ["Jay", "Kati", "Manu"]
    if current_fish_data:
        available_fish = [
            fish for fish in current_fish_data
            if fish.get("name") not in starter_names and fish.get("health", 0) > 0
        ]
        return {"success": True, "fish": available_fish, "count": len(available_fish)}
    dummy_fish = [
        {"index": 0, "name": "Bubbles", "species": "Goldfish", "health": 75, "max_health": 100},
        {"index": 1, "name": "Finn", "species": "Betta", "health": 50, "max_health": 100},
        {"index": 2, "name": "Coral", "species": "Clownfish", "health": 25, "max_health": 100},
        {"index": 3, "name": "Marina", "species": "Angelfish", "health": 90, "max_health": 100}
    ]
    return {"success": True, "fish": dummy_fish, "count": len(dummy_fish)}

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
    disconnected = []
    for client in connected_clients:
        try:
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
    if not verify_twitch_signature(
        body,
        twitch_eventsub_message_signature,
        twitch_eventsub_message_id,
        twitch_eventsub_message_timestamp
    ):
        print("❌ Invalid signature")
        return {"error": "Invalid signature"}, 403
    data = await request.json()
    if twitch_eventsub_message_type == "webhook_callback_verification":
        print("✓ Webhook verification request received")
        return PlainTextResponse(content=data["challenge"])
    elif twitch_eventsub_message_type == "notification":
        event_type = data.get("subscription", {}).get("type")
        if event_type == "channel.subscribe":
            event_data = data["event"]
            username = event_data["user_name"]
            user_id = event_data["user_id"]
            tier = event_data["tier"]
            hp = SUBSCRIPTION_HP.get(tier, 100)
            tier_name = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(tier, "Tier 1")
            print(f"✓ New subscription: {username} ({tier_name}) - {hp} HP fish")
            disconnected = []
            for client in connected_clients:
                try:
                    await client.send_text(f"subscription:{username}:{tier}:{hp}")
                    print(f"  → Sent subscription to Godot")
                except:
                    disconnected.append(client)
            for client in disconnected:
                connected_clients.remove(client)
            if username not in fish_registry:
                fish_registry[username] = []
            fish_registry[username].append(username)
        elif event_type == "channel.channel_points_custom_reward_redemption.add":
            event_data = data["event"]
            reward_id = event_data["reward"]["id"]
            redemption_id = event_data["id"]
            username = event_data["user_name"]
            user_id = event_data["user_id"]
            user_input = event_data.get("user_input", "")
            if username not in redemption_tracker:
                redemption_tracker[username] = {}
            redemption_tracker[username][reward_id] = redemption_id
            button_id = REWARD_ID_TO_BUTTON.get(reward_id)
            if not button_id:
                print(f"⚠️ Unknown reward redeemed: {reward_id}")
                return {"status": "unknown_reward"}
            print(f"✓ {username} redeemed reward (Button {button_id})")
            if button_id == 2:
                fish_name = user_input.strip()
                if not fish_name:
                    print(f"⚠️ No fish name provided by {username}")
                    return {"status": "invalid_input"}
                disconnected = []
                for client in connected_clients:
                    try:
                        await client.send_text(f"button:{button_id}:user:{username}:fish_name:{fish_name}:reward:{reward_id}")
                        print(f"  → Sent feed fish '{fish_name}' command")
                    except:
                        disconnected.append(client)
                for client in disconnected:
                    connected_clients.remove(client)
            else:
                disconnected = []
                for client in connected_clients:
                    try:
                        await client.send_text(f"button:{button_id}:user:{username}:reward:{reward_id}")
                        print(f"  → Sent to Godot client")
                    except:
                        disconnected.append(client)
                for client in disconnected:
                    connected_clients.remove(client)
            if button_id == 6:
                if username not in fish_registry:
                    fish_registry[username] = []
                fish_registry[username].append(username)
            return {"status": "executed", "button": button_id, "username": username}
    return {"status": "ok"}

@app.post("/game-event")
async def game_event(event: GameEvent):
    if event.api_key != GAME_API_KEY:
        return {"error": "Unauthorized"}, 401

    player_hash = hash_steam_id(event.steam_id)
    print(f"[GameEvent] event={event.event_name or event.achievement} day={event.day} hash={player_hash[:8]}...")

    forward_to_zapier(player_hash, event)

    return {"status": "ok"}

@app.get("/has-fish")
async def has_fish(username: str):
    has = username in fish_registry and len(fish_registry[username]) > 0
    return {"has_fish": has, "username": username}

@app.get("/fish-list")
async def get_fish_list():
    """Legacy endpoint - redirects to /fish"""
    return await get_fish()

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