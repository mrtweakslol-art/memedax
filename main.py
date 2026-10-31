import os
import re
import asyncio
import aiohttp
from flask import Flask
import threading
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from supabase import create_client, Client

# --- 1. CONFIGURATION (USE ENVIRONMENT VARIABLES ON RENDER) ---
API_ID = os.environ.get('API_ID', 'YOUR_API_ID')
API_HASH = os.environ.get('API_HASH', 'YOUR_API_HASH')
SESSION_STRING = os.environ.get('SESSION_STRING', 'YOUR_SESSION_STRING')
SUPABASE_URL = f"https://{os.environ.get('SUPABASE_PROJECT_ID', 'srqxksonacfhfemketxi')}.supabase.co"
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'YOUR_SUPABASE_ANON_KEY')
CHANNEL_USERNAME = 'SolanaSmartMoney_Signals'

# --- 2. INITIALIZE SERVICES ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is running and monitoring Solana signals!"

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# --- 3. HELPER FUNCTIONS ---
async def get_direct_image_url(post_url):
    try:
        web_url = post_url.replace('t.me/', 't.me/s/')
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        async with aiohttp.ClientSession() as session:
            async with session.get(web_url, headers=headers) as response:
                html = await response.text()
                match = re.search(r'<meta property="og:image" content="(.*?)"', html)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"⚠️ Image fetch error: {e}")
    return None

def parse_new_signal(text):
    if not text: return {}
    data = {}
    data['coin_symbol'] = re.search(r'\$(\w+)', text)
    data['coin_symbol'] = f"${data['coin_symbol'].group(1)}" if data['coin_symbol'] else None
    
    ca_match = re.search(r'CA:\s*\n?`?(\w+)`?', text)
    data['contract_address'] = ca_match.group(1) if ca_match else None
    
    mc_match = re.search(r'MC:\s*\$?([\d.]+[KMB]?)', text)
    data['market_cap'] = mc_match.group(1) if mc_match else None
    
    liq_match = re.search(r'Liq(?:uidity)?:\s*\$?([\d.]+[KMB]?)', text)
    data['liquidity'] = liq_match.group(1) if liq_match else None
    
    score_match = re.search(r'Score:\s*(\d+/\d+)', text)
    data['score'] = score_match.group(1) if score_match else None
    return data

def parse_gain_update(text):
    """Extracts gain info and the original message ID from an update post"""
    data = {}
    
    # 1. Find the original message ID from the link (e.g., .../44000)
    id_match = re.search(r't\.me/[a-zA-Z0-9_]+/(\d+)', text)
    data['original_message_id'] = int(id_match.group(1)) if id_match else None
    
    # 2. Extract Gain (matches "+50%", "X2", "X5.5", etc.)
    gain_match = re.search(r'Gain:\s*\+?(\d+%|X[\d.]+)', text, re.IGNORECASE)
    data['gain_percentage'] = gain_match.group(1) if gain_match else None
    
    # 3. Extract Time
    time_match = re.search(r'⏱\s*Time:\s*(.+?)(?:\n|$)', text)
    data['gain_time'] = time_match.group(1).strip() if time_match else None
    
    # 4. Extract Next Target
    target_match = re.search(r'🎯\s*Next Target:\s*(.+?)(?:\n|$)', text)
    data['next_target'] = target_match.group(1).strip() if target_match else None
    
    return data

# --- 4. MAIN BOT LOGIC ---
@client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def handler(event):
    message = event.message
    text = message.text or ""

    # CHECK 1: Is this a GAIN UPDATE message?
    if "Entry Signal" in text and "t.me/" in text:
        update_data = parse_gain_update(text)
        original_id = update_data.get('original_message_id')
        
        if original_id and update_data.get('gain_percentage'):
            print(f"📈 GAIN UPDATE DETECTED! Updating original message #{original_id}")
            print(f"   Gain: {update_data['gain_percentage']} | Time: {update_data['gain_time']}")
            
            try:
                # Update the EXISTING row in Supabase
                response = supabase.table('signals').update({
                    'is_updated': True,
                    'gain_percentage': update_data['gain_percentage'],
                    'gain_time': update_data['gain_time'],
                    'next_target': update_data['next_target']
                }).eq('message_id', original_id).execute()
                print(f"✅ Successfully updated original signal #{original_id} in Supabase!")
            except Exception as e:
                print(f"❌ Supabase Update Error: {e}")
        return # Stop here, it's an update, not a new signal

    # CHECK 2: Is this a NEW SIGNAL? (Must have a photo)
    if not message.photo:
        return

    post_url = f"https://t.me/{CHANNEL_USERNAME}/{message.id}"
    print(f"\n📨 NEW SIGNAL DETECTED! Post: {message.id}")
    
    # Get Image & Parse Text
    image_url = await get_direct_image_url(post_url)
    parsed_data = parse_new_signal(text)
    
    print(f"🖼️ Image: {image_url}")
    print(f"💰 Coin: {parsed_data.get('coin_symbol')}")

    # Insert NEW row into Supabase
    try:
        data_to_insert = {
            'message_id': message.id,
            'post_url': post_url,
            'image_url': image_url,
            'coin_symbol': parsed_data.get('coin_symbol'),
            'contract_address': parsed_data.get('contract_address'),
            'market_cap': parsed_data.get('market_cap'),
            'liquidity': parsed_data.get('liquidity'),
            'score': parsed_data.get('score'),
            'raw_text': text,
            'timestamp': message.date.isoformat() if message.date else None,
            'is_updated': False # Starts as false
        }
        
        response = supabase.table('signals').insert(data_to_insert).execute()
        print(f"✅ Saved new signal to Supabase successfully!")
        
    except Exception as e:
        print(f"❌ Supabase Insert Error: {e}")

async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    print("🚀 Starting Telegram Client...")
    await client.start()
    print(f"✅ Logged in as {(await client.get_me()).first_name}")
    print(f"👀 Monitoring {CHANNEL_USERNAME} for Alerts AND Gain Updates...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())