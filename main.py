import os
import re
import asyncio
import threading
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from flask import Flask, jsonify
from flask_cors import CORS

# --- 1. EVENT LOOP FIX ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# --- 2. CREDENTIALS ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

CHANNEL_1 = -1003671906331
CHANNEL_2 = '@pocket_option_signal_Quotex'
TARGET_CHATS = [CHANNEL_1, CHANNEL_2]

# --- 3. IN-MEMORY SIGNAL STORE (Replaces Supabase) ---
latest_signal = {
    "id": 0,
    "asset": "",
    "direction": "",
    "expiry": "",
    "timestamp": "",
    "source": ""
}

# --- 4. FLASK API SERVER (For Extension to read) ---
app = Flask(__name__)
CORS(app) # Allows your browser extension to fetch this data

@app.route('/')
def home():
    return "✅ OTC FLIPGOD Bot is running!"

@app.route('/api/latest-signal', methods=['GET'])
def get_signal():
    """The extension will fetch this URL to get the newest signal"""
    return jsonify(latest_signal)

def run_web_server():
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

# Start Flask in background
threading.Thread(target=run_web_server, daemon=True).start()

# --- 5. TELEGRAM CLIENT ---
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)
pending_signals = {}

# --- 6. PARSER ---
def parse_single_message_signal(text):
    text = text.strip()
    pattern = r'([A-Z]{3,6}/?[A-Z]{3,6})\s+(\d+)\s+(min|minute|minutes)\s+(up|down|buy|sell|call|put)'
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    
    pair = match.group(1).replace('/', '').upper()
    duration = match.group(2)
    direction_raw = match.group(4).upper()
    direction = "UP" if direction_raw in ['UP', 'BUY', 'CALL'] else "DOWN"
    
    return {"asset": pair, "expiry": f"{duration}M", "direction": direction}

# --- 7. MESSAGE HANDLER ---
@client.on(events.NewMessage(chats=TARGET_CHATS))
async def message_handler(event):
    global latest_signal
    
    text = event.raw_text.strip()
    chat_id = event.chat_id
    chat_title = event.chat.title if hasattr(event.chat, 'title') and event.chat.title else str(chat_id)
    
    print(f"\n📨 RAW MESSAGE from [{chat_id}]: '{text[:80]}'")
    
    if not text or len(text) < 2:
        return
    
    text_upper = text.upper()
    
    # Ignore Win/Loss messages for auto-trading
    if any(w in text_upper for w in ['WIN', 'PROFIT', 'LOSS', '❌', '💰']):
        return
    
    # FORMAT 1: Single Message
    if chat_id == CHANNEL_1:
        signal = parse_single_message_signal(text)
        if signal:
            latest_signal = {
                "id": latest_signal["id"] + 1,
                "asset": signal["asset"],
                "direction": signal["direction"],
                "expiry": signal["expiry"],
                "timestamp": datetime.now().isoformat(),
                "source": chat_title
            }
            print(f"🎯 NEW SIGNAL SAVED TO API: {latest_signal['asset']} {latest_signal['direction']} {latest_signal['expiry']}")
        return

    # FORMAT 2: Multi-Step
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
            latest_signal = {
                "id": latest_signal["id"] + 1,
                "asset": pending_signals[chat_id]["asset"],
                "direction": pending_signals[chat_id]["direction"],
                "expiry": pending_signals[chat_id]["expiry"],
                "timestamp": datetime.now().isoformat(),
                "source": chat_title
            }
            print(f"🎯 NEW SIGNAL SAVED TO API: {latest_signal['asset']} {latest_signal['direction']} {latest_signal['expiry']}")
            del pending_signals[chat_id]
        return

# --- 8. START ---
async def main():
    print("🚀 Pocket Option Signal Bot Starting (Direct to Extension Mode)...")
    try:
        await client.start()
        me = await client.get_me()
        print(f"✅ SUCCESS! Connected as: {me.first_name}")
        print(f"🌐 API available at: https://YOUR-RENDER-URL.onrender.com/api/latest-signal")
        print("⏳ Waiting for signals...")
        await client.run_until_disconnected()
    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")

if __name__ == "__main__":
    loop.run_until_complete(main())
