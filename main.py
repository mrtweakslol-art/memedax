import os
import re
import asyncio
import threading
import traceback
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from supabase import create_client, Client
from flask import Flask

# --- 1. EVENT LOOP FIX ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# --- 2. CREDENTIALS ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# DEBUG: Print masked credentials to verify they loaded
print(f"🔍 DEBUG: API_ID={API_ID}, API_HASH starts with: {API_HASH[:5] if API_HASH else 'MISSING'}")
print(f"🔍 DEBUG: SESSION_STRING starts with: {SESSION_STRING[:15] if SESSION_STRING else 'MISSING'}")
print(f"🔍 DEBUG: SUPABASE_URL starts with: {SUPABASE_URL[:25] if SUPABASE_URL else 'MISSING'}")

CHANNEL_1 = -1003671906331
CHANNEL_2 = '@pocket_option_signal_Quotex'
TARGET_CHATS = [CHANNEL_1, CHANNEL_2]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

pending_signals = {}

# --- 3. KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home():
    return "✅ Bot is running!"

def run_web_server():
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

threading.Thread(target=run_web_server, daemon=True).start()

# --- 4. DATABASE FUNCTIONS ---
async def insert_signal(data):
    try:
        print(f"💾 Attempting to insert into Supabase: {data['asset']} {data['direction']}")
        response = await supabase.table("signals").insert(data).execute()
        new_id = response.data[0]['id']
        print(f"✅ SUCCESS: Inserted ID={new_id}")
        return new_id
    except Exception as e:
        print(f"❌ SUPABASE INSERT FAILED: {e}")
        return None

async def update_latest_result(group_name, status):
    try:
        response = await supabase.table("signals").select("id").eq("group_name", group_name).or_("status.eq.PENDING,status.eq.ACTIVE,status.eq.WAITING").order("timestamp", desc=True).limit(1).execute()
        if response.data and len(response.data) > 0:
            signal_id = response.data[0]['id']
            await supabase.table("signals").update({"status": status}).eq("id", signal_id).execute()
            print(f"✅ Updated signal {signal_id} to {status}")
    except Exception as e:
        print(f"❌ UPDATE FAILED: {e}")

# --- 5. PARSER ---
def parse_single_message_signal(text):
    text = text.strip()
    pattern = r'([A-Z]{3,6}/?[A-Z]{3,6})\s+(\d+)\s+(min|minute|minutes)\s+(up|down|buy|sell|call|put)'
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, "Regex did not match"
    
    pair = match.group(1).replace('/', '').upper()
    duration = match.group(2)
    direction_raw = match.group(4).upper()
    direction = "UP" if direction_raw in ['UP', 'BUY', 'CALL'] else "DOWN"
    
    return {"asset": pair, "expiry": f"{duration}M", "direction": direction}, "Success"

# --- 6. MESSAGE HANDLER ---
@client.on(events.NewMessage(chats=TARGET_CHATS))
async def message_handler(event):
    text = event.raw_text.strip()
    chat_id = event.chat_id
    chat_title = event.chat.title if hasattr(event.chat, 'title') and event.chat.title else str(chat_id)
    
    print(f"\n📨 RAW MESSAGE from [{chat_id}] {chat_title}: '{text[:80]}'")
    
    if not text or len(text) < 2:
        return
    
    text_upper = text.upper()
    if any(w in text_upper for w in ['WIN', 'PROFIT', '+93%', '+100%']) or '💰' in text or '✅' in text:
        print("🔔 Detected WIN")
        await update_latest_result(chat_title, "WIN")
        return
    if 'LOSS' in text_upper or '❌' in text or '💔' in text:
        print("🔻 Detected LOSS")
        await update_latest_result(chat_title, "LOSS")
        return
    
    if chat_id == CHANNEL_1:
        signal, reason = parse_single_message_signal(text)
        if signal:
            payload = {
                "group_name": chat_title, "type": "SIGNAL", "asset": signal["asset"],
                "direction": signal["direction"], "expiry": signal["expiry"],
                "investment": "UNKNOWN", "status": "ACTIVE",
                "timestamp": datetime.now().isoformat(), "raw_message": text
            }
            print(f"🎯 PARSED: {signal['asset']} {signal['direction']} {signal['expiry']}")
            await insert_signal(payload)
        else:
            print(f"❌ PARSE FAILED: {reason}")
        return

    if chat_id == CHANNEL_2 or 'pocket_option_signal' in str(chat_title).lower().replace(' ', ''):
        if chat_id not in pending_signals:
            pending_signals[chat_id] = {"asset": None, "expiry": None, "direction": None}
        
        asset_match = re.search(r'([A-Z]{3,6}/?[A-Z]{3,6})\s*(OTC)?', text_upper)
        if asset_match and not pending_signals[chat_id]["asset"]:
            pending_signals[chat_id]["asset"] = asset_match.group(1).replace('/', '')
            print(f"📝 Step 1/3: Asset = {pending_signals[chat_id]['asset']}")
            return
        
        expiry_match = re.search(r'(\d+)\s*(MIN|MINUTE|MINUTES)', text_upper)
        if expiry_match and pending_signals[chat_id]["asset"] and not pending_signals[chat_id]["expiry"]:
            pending_signals[chat_id]["expiry"] = f"{expiry_match.group(1)}M"
            print(f"📝 Step 2/3: Expiry = {pending_signals[chat_id]['expiry']}")
            return
        
        if any(d in text_upper for d in ['UP', 'BUY', 'CALL']) and pending_signals[chat_id]["asset"] and pending_signals[chat_id]["expiry"]:
            pending_signals[chat_id]["direction"] = "UP"
        elif any(d in text_upper for d in ['DOWN', 'SELL', 'PUT']) and pending_signals[chat_id]["asset"] and pending_signals[chat_id]["expiry"]:
            pending_signals[chat_id]["direction"] = "DOWN"
            
        if pending_signals[chat_id]["direction"]:
            payload = {
                "group_name": chat_title, "type": "SIGNAL", "asset": pending_signals[chat_id]["asset"],
                "direction": pending_signals[chat_id]["direction"], "expiry": pending_signals[chat_id]["expiry"],
                "investment": "UNKNOWN", "status": "ACTIVE",
                "timestamp": datetime.now().isoformat(), "raw_message": f"{pending_signals[chat_id]['asset']} {pending_signals[chat_id]['expiry']} {pending_signals[chat_id]['direction']}"
            }
            print(f"🎯 MULTI-STEP PARSED: {payload['asset']} {payload['direction']} {payload['expiry']}")
            await insert_signal(payload)
            del pending_signals[chat_id]
        return

# --- 7. START WITH ERROR HANDLING ---
async def main():
    print("🚀 Pocket Option Signal Bot Starting...")
    print(f"📡 Listening to {len(TARGET_CHATS)} channels...")
    print("=" * 50)
    
    try:
        print("🔄 Attempting to connect to Telegram...")
        await client.start()
        me = await client.get_me()
        print(f"✅ SUCCESS! Connected as: {me.first_name} (@{me.username})")
        print("⏳ Waiting for signals...")
        await client.run_until_disconnected()
    except Exception as e:
        print(f"❌ FATAL ERROR connecting to Telegram!")
        print(f"Error details: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    loop.run_until_complete(main())
