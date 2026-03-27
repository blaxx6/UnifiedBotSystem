# whatsapp_evolution.py (Persistent Event Loop + LRU Dedup + Rate Limiting)
from flask import Flask, request, jsonify
import requests
import os
import asyncio
import traceback
import json
from collections import OrderedDict
from bot_handler import BotHandler
from config import Config
from database import db
from contacts_manager import log_contact
import base64
from whatsapp_decryption import decrypt_media
import mimetypes
import threading
import time as import_time
import logging
from whatsapp_payload import parse_webhook_payload, build_quoted_payload, classify_jid

# Configure File Logging
logging.basicConfig(
    filename='debug_whatsapp.log', 
    level=logging.DEBUG, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.info("🚀 WhatsApp Service Started")


app = Flask(__name__)

# --- INITIALIZATION ---
handler = None
pending_lid_resolutions = {}
try:
    print("🔄 Initializing Bot Handler...")
    handler = BotHandler()
    print("✅ Bot Handler Initialized!")
    logging.info("✅ BotHandler Initialized!")
except Exception as e:
    print(f"❌ CRITICAL: BotHandler Failed: {e}")
    logging.error(f"❌ CRITICAL: BotHandler Failed: {e}")

class EvolutionAPI:
    def __init__(self):
        self.base_url = Config.EVOLUTION_API_URL
        self.api_key = Config.EVOLUTION_API_KEY
        self.instance = Config.EVOLUTION_INSTANCE_NAME
        self.headers = {'apikey': self.api_key, 'Content-Type': 'application/json'}

    def resolve_target_jid(self, incoming_jid, push_name):
        """
        RESOLVER with PROFILE PIC FINGERPRINTING:
        1. Check DB cache (fastest).
        2. Match profile picture URL fingerprint (most reliable).
        3. Name matching as last fallback (strict then fuzzy).
        4. FALLBACK: Return the LID.
        """
        import re
        
        # 1. DB CACHE CHECK
        if "@lid" in incoming_jid:
            cached_jid = db.get_real_jid_from_lid(incoming_jid)
            if cached_jid:
                print(f"✅ DB MAPPING FOUND: {incoming_jid} -> {cached_jid}")
                return cached_jid

        # If already a phone JID, use directly
        if "@s.whatsapp.net" in incoming_jid:
            return incoming_jid

        # 2. API RESOLUTION (for LIDs only)
        if "@lid" in incoming_jid:
            try:
                url = f"{self.base_url}/chat/findContacts/{self.instance}"
                r = requests.post(url, headers=self.headers)
                
                if r.status_code == 200:
                    contacts = r.json()
                    
                    # Helper: Extract pic fingerprint from URL
                    def get_pic_id(pic_url):
                        if not pic_url: return None
                        m = re.search(r'/([0-9]+_[0-9]+_[0-9]+_n\.jpg)', pic_url)
                        return m.group(1) if m else None
                    
                    # Find the LID contact's profile pic
                    lid_pic_id = None
                    for c in contacts:
                        c_jid = c.get('remoteJid') or c.get('id') or ''
                        if c_jid == incoming_jid:
                            lid_pic_id = get_pic_id(c.get('profilePicUrl'))
                            break
                    
                    # --- METHOD A: PROFILE PIC FINGERPRINT MATCH ---
                    if lid_pic_id:
                        for c in contacts:
                            jid = c.get('remoteJid') or c.get('id') or ''
                            if "@s.whatsapp.net" not in jid: continue
                            if jid == Config.BOT_PHONE_NUMBER: continue
                            
                            contact_pic_id = get_pic_id(c.get('profilePicUrl'))
                            if contact_pic_id and contact_pic_id == lid_pic_id:
                                print(f"✅ PIC MATCH: {incoming_jid} -> {jid} (pic={lid_pic_id[:20]}...)")
                                db.save_lid_mapping(incoming_jid, jid, push_name)
                                return jid
                    
            except Exception as e:
                print(f"⚠️ Auto-Resolve Error: {e}")

        # 4. FALLBACK
        print(f"⚠️ Could not resolve {incoming_jid}.")
        return None

    def send_text(self, jid, message, quoted_payload=None):
        url = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {"number": jid, "text": message}
        
        # MANDATORY: If sending to a LID, we MUST provide the quote
        if quoted_payload: 
            payload["quoted"] = quoted_payload

        try:
            print(f"📤 Sending to {jid}...")
            logging.info(f"📤 Attempting to send message to: {jid}")

            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code in [200, 201]:
                print("✅ Message Sent Successfully!")
                db.save_message(platform='whatsapp', user_id=jid, user_name="Bot", message_text=message, direction='outgoing')
            else:
                error_msg = f"❌ SEND FAILED: {r.status_code} - {r.text}"
                print(error_msg)
                logging.error(error_msg)
        except Exception as e:
            print(f"❌ Connection Error: {e}")
            logging.error(f"❌ Connection Error: {e}")

    def send_audio(self, jid, audio_path, caption="", quoted_payload=None):
        url = f"{self.base_url}/message/sendMedia/{self.instance}"
        try:
            with open(audio_path, 'rb') as audio_file:
                audio_base64 = base64.b64encode(audio_file.read()).decode('utf-8')
            
            mime = mimetypes.guess_type(audio_path)[0] or 'audio/mpeg'
            payload = {
                "number": jid, "mediatype": "audio", "mimetype": mime, 
                "media": audio_base64, "caption": caption, "fileName": os.path.basename(audio_path)
            }
            if quoted_payload: payload["quoted"] = quoted_payload

            logging.info(f"📤 Attempting to send AUDIO to: {jid}")


            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code in [200, 201]:
                print("✅ Audio Sent Successfully!")
                db.save_message(platform='whatsapp', user_id=jid, user_name="Bot", message_text=f"[Audio] {caption}", direction='outgoing', message_type='audio', audio_path=audio_path)
        except Exception as e:
            print(f"❌ Audio Error: {e}")

    def send_typing_indicator(self, jid: str):
        """Send 'composing' presence to show typing indicator."""
        try:
            url = f"{self.base_url}/chat/updatePresence/{self.instance}"
            requests.post(url, json={"number": jid, "presence": "composing"},
                          headers=self.headers, timeout=3)
        except Exception as e:
            logging.debug(f"Typing indicator failed (non-fatal): {e}")

    def send_paused_indicator(self, jid: str):
        """Send 'paused' presence to clear typing indicator."""
        try:
            url = f"{self.base_url}/chat/updatePresence/{self.instance}"
            requests.post(url, json={"number": jid, "presence": "paused"},
                          headers=self.headers, timeout=3)
        except Exception as e:
            logging.debug(f"Paused indicator failed (non-fatal): {e}")

    def send_image(self, jid, image_path, caption="", quoted_payload=None):
        url = f"{self.base_url}/message/sendMedia/{self.instance}"
        try:
            with open(image_path, 'rb') as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
            
            mime = mimetypes.guess_type(image_path)[0] or 'image/jpeg'
            payload = {
                "number": jid, "mediatype": "image", "mimetype": mime, 
                "media": img_base64, "caption": caption, "fileName": os.path.basename(image_path)
            }
            if quoted_payload: payload["quoted"] = quoted_payload

            logging.info(f"📤 Attempting to send IMAGE to: {jid}")

            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code in [200, 201]:
                print("✅ Image Sent Successfully!")
                db.save_message(platform='whatsapp', user_id=jid, user_name="Bot", message_text=f"[Image] {caption}", direction='outgoing', message_type='image') #, media_path=image_path)
            else:
                print(f"❌ IMAGE SEND FAILED: {r.status_code} - {r.text}")
        except Exception as e:
            print(f"❌ Image Send Error: {e}")

    def get_base64_from_message(self, message_id):
        """
        Fetches the Base64 content of a media message directly from Evolution API.
        This is crucial for encrypted media (like WhatsApp audio) where the URL is not directly usable.
        """
        url = f"{self.base_url}/chat/getBase64FromMediaMessage/{self.instance}"
        payload = {
            "message": {
                "key": {
                    "id": message_id
                }
            },
            "convertToMp4": False  # We want original audio/image
        }

        try:
            logging.info(f"📥 Fetching Base64 for message {message_id}...")
            r = requests.post(url, json=payload, headers=self.headers)
            logging.info(f"🔍 API Response [{r.status_code}]: {r.text[:500]}")  # Log first 500 chars

            if r.status_code == 200:
                data = r.json()
                if 'base64' in data:
                    logging.info("✅ Base64 fetched successfully!")
                    return data['base64']
                
                logging.warning(f"⚠️ No 'base64' key in response: {data.keys()}")
            else:
                logging.error(f"❌ Base64 Fetch Failed: {r.status_code} - {r.text}")
                
            # FALLBACK
            logging.info("🔄 Trying fallback: findMessage...")
            fallback_url = f"{self.base_url}/chat/findMessage/{self.instance}"
            fallback_payload = {"messageId": message_id}
            
            r2 = requests.post(fallback_url, json=fallback_payload, headers=self.headers)
            logging.info(f"🔍 Fallback Response [{r2.status_code}]: {r2.text[:500]}")

            if r2.status_code == 200:
                 data2 = r2.json()
                 if 'base64' in data2:
                     logging.info("✅ Base64 fetched via findMessage!")
                     return data2['base64']
                 elif 'message' in data2 and 'base64' in data2['message']:
                     return data2['message']['base64']
                     
            logging.error(f"❌ Fallback findMessage failed too: {r2.status_code}")

        except Exception as e:
            logging.error(f"❌ Base64 API Error: {e}")
        return None

evolution = EvolutionAPI()


# --- HELPER FUNCTIONS ---

def run_async_media_process(target_jid, original_jid, media_type, media_msg, quoted_payload, message_id):
    """Helper to download media and run async handler"""
    try:
        logging.info(f"📥 Processing {media_type.upper()}...")
        
        # 1. PREPARE PATHS
        if not os.path.exists(Config.TEMP_DIR):
            os.makedirs(Config.TEMP_DIR)
            
        timestamp = str(int(import_time.time()))
        ext = 'mp3' if media_type == 'audio' else 'jpg'
        temp_path = os.path.join(Config.TEMP_DIR, f"temp_{message_id}_{timestamp}.{ext}")

        # 2. GET MEDIA CONTENT (Prefer Querying API for Base64)
        # WhatsApp URLs are often encrypted, so we ask Evolution for the decrypted base64
        
        b64_data = media_msg.get('jpegThumbnail') if media_type == 'image' else None
        media_url = media_msg.get('url')
        
        # If it's audio, or if we need high-res image but don't have it, fetch from API
        fetched_b64 = None
        
        # Condition: If Audio, OR if Image but we want full quality (thumbnail is small)
        if media_type == 'audio' or (media_type == 'image' and not b64_data):
            fetched_b64 = evolution.get_base64_from_message(message_id)
            
            # --- MANUAL DECRYPTION FALLBACK ---
            if not fetched_b64:
                 media_key = media_msg.get('mediaKey')
                 url = media_msg.get('url')
                 if media_key and url:
                     logging.info("🔐 Attempting Manual Decryption...")
                     decrypted_bytes = decrypt_media(url, media_key, media_type)
                     if decrypted_bytes:
                         logging.info("✅ Manual Decryption Successful!")
                         # Check if we need to re-encode to Base64 to fit existing logic
                         # The logic below expects b64_data to be present to write to file.
                         # We can just write it here and set b64_data to None check, BUT
                         # the logic below (lines 272+) checks `if b64_data`.
                         # So let's re-encode to Base64 to keep flow simple.
                         fetched_b64 = base64.b64encode(decrypted_bytes).decode('utf-8')
                     else:
                        logging.error("❌ Manual Decryption Failed.")

        if fetched_b64:
            b64_data = fetched_b64

        # 3. SAVE AND PROCESS
        if b64_data:
            # Case A: We have Base64 (Best for Audio & Images)
            with open(temp_path, 'wb') as f:
                f.write(base64.b64decode(b64_data))
                
            if media_type == 'image':
                _run_in_loop(handle_image_message(target_jid, original_jid, temp_path, quoted_payload))
            elif media_type == 'audio':
                _run_in_loop(handle_audio_message(target_jid, original_jid, temp_path, quoted_payload))

        elif media_url and "mmg.whatsapp.net" not in media_url and ".enc" not in media_url:
            # Case B: We have a clean public URL (Rare for WhatsApp, but possible for other providers)
            # Only use if NOT encrypted WhatsApp URL
            r = requests.get(media_url)
            with open(temp_path, 'wb') as f:
                f.write(r.content)
                
            if media_type == 'image':
                _run_in_loop(handle_image_message(target_jid, original_jid, temp_path, quoted_payload))
            elif media_type == 'audio':
                _run_in_loop(handle_audio_message(target_jid, original_jid, temp_path, quoted_payload))
        else:
            # Case C: Failure
            msg = f"⚠️ Could not download {media_type}. URL is encrypted and API did not return Base64."
            evolution.send_text(target_jid, msg, quoted_payload)
            logging.error("❌ Media Download Failed: No valid B64 or Public URL.")
    except Exception as e:
        logging.error(f"❌ Media Process Error: {e}")
        traceback.print_exc()

async def handle_image_message(target_jid, original_jid, img_path, quoted_msg, push_name=""):
    clean_number = target_jid.split('@')[0]
    result = await handler.process_image_message(clean_number, img_path, user_name=push_name)
    evolution.send_text(target_jid, result['message'], quoted_payload=quoted_msg)

async def handle_audio_message(target_jid, original_jid, audio_path, quoted_msg, push_name=""):
    clean_number = target_jid.split('@')[0]
    result = await handler.process_voice_message(clean_number, audio_path, user_name=push_name)
    if result['type'] == 'audio':
        evolution.send_audio(target_jid, result['audio_path'], caption=result['message'], quoted_payload=quoted_msg)
    else:
        evolution.send_text(target_jid, result['message'], quoted_payload=quoted_msg)

def run_async_process(target_jid, original_jid, text_message, quoted_payload, push_name=""):
    """Submit async handler to the persistent event loop."""
    try:
        _run_in_loop(handle_text_message(target_jid, original_jid, text_message, quoted_payload, push_name))
    except Exception as e:
        print(f"❌ Background Process Error: {e}")
        logging.error(f"❌ Background Process Error: {e}")


async def handle_text_message(target_jid, original_jid, text, quoted_msg, push_name=""):
    if text.startswith('/'): return
    
    # Show typing indicator immediately (masks LLM latency)
    evolution.send_typing_indicator(target_jid)
    
    clean_number = target_jid.split('@')[0]
    result = await handler.process_text_message(clean_number, text, user_name=push_name)
    
    # Clear typing indicator after response
    evolution.send_paused_indicator(target_jid)
    
    if result['type'] == 'audio':
        evolution.send_audio(target_jid, result['audio_path'], caption=result['message'], quoted_payload=quoted_msg)
    else:
        evolution.send_text(target_jid, result['message'], quoted_payload=quoted_msg)


# ── PERSISTENT EVENT LOOP ────────────────────────────────────────────
# Single event loop running in a daemon thread. All async work is
# submitted via _run_in_loop() instead of creating per-request loops.
_bg_loop = asyncio.new_event_loop()

def _start_bg_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

_bg_thread = threading.Thread(target=_start_bg_loop, args=(_bg_loop,), daemon=True)
_bg_thread.start()

def _run_in_loop(coro):
    """Submit a coroutine to the persistent event loop and wait for result."""
    future = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return future.result()  # blocks calling thread until done


# ── LRU DEDUP CACHE ──────────────────────────────────────────────────
# OrderedDict-based LRU: evicts oldest entries instead of clearing all.
DEDUP_MAX_SIZE = 2000
_dedup_cache: OrderedDict[str, float] = OrderedDict()
_dedup_lock = threading.Lock()

def _is_duplicate(message_id: str) -> bool:
    """Check if message_id was recently seen. Returns True if duplicate."""
    if not message_id:
        return False
    with _dedup_lock:
        if message_id in _dedup_cache:
            return True
        _dedup_cache[message_id] = import_time.time()
        # Evict oldest entries when over capacity
        while len(_dedup_cache) > DEDUP_MAX_SIZE:
            _dedup_cache.popitem(last=False)
        return False


# ── RATE LIMITING ────────────────────────────────────────────────────
# Per-user sliding window: max RATE_LIMIT_MAX messages per RATE_LIMIT_WINDOW_SEC.
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SEC = 60
_rate_windows: dict[str, list[float]] = {}  # {jid: [timestamps]}
_rate_lock = threading.Lock()

def _is_rate_limited(jid: str) -> bool:
    """Returns True if user exceeded rate limit."""
    now = import_time.time()
    with _rate_lock:
        window = _rate_windows.get(jid, [])
        # Prune old timestamps
        window = [t for t in window if now - t < RATE_LIMIT_WINDOW_SEC]
        if len(window) >= RATE_LIMIT_MAX:
            _rate_windows[jid] = window
            return True
        window.append(now)
        _rate_windows[jid] = window
        return False


# ── DEBOUNCE SYSTEM ──────────────────────────────────────────────────
# Prevents double replies when users send rapid-fire messages.
# Buffers messages per user for 5s, then processes all at once.
DEBOUNCE_SECONDS = 5.0
_pending_messages = {}   # {target_jid: [list of (text, quoted_payload, push_name)]}
_debounce_timers = {}    # {target_jid: Timer}
_debounce_lock = threading.Lock()

def _debounce_and_process(target_jid, incoming_jid, text, quoted_payload, push_name):
    """Buffer messages per user. After 3s of silence, process all buffered as one."""
    with _debounce_lock:
        # Cancel previous timer for this user
        if target_jid in _debounce_timers:
            _debounce_timers[target_jid].cancel()
        
        # Append message to buffer
        if target_jid not in _pending_messages:
            _pending_messages[target_jid] = []
        _pending_messages[target_jid].append((text, quoted_payload, push_name, incoming_jid))
        
        # Start new timer — fires after DEBOUNCE_SECONDS of silence
        timer = threading.Timer(
            DEBOUNCE_SECONDS,
            _flush_debounce,
            args=(target_jid,)
        )
        _debounce_timers[target_jid] = timer
        timer.start()

def _flush_debounce(target_jid):
    """Called after debounce timeout. Combines buffered messages and processes."""
    with _debounce_lock:
        msgs = _pending_messages.pop(target_jid, [])
        _debounce_timers.pop(target_jid, None)
    
    if not msgs:
        return
    
    # Combine all buffered messages into one text
    texts = [m[0] for m in msgs]
    combined_text = " ".join(texts)
    # Use the last message's quoted_payload and push_name
    _, quoted_payload, push_name, incoming_jid = msgs[-1]
    
    if len(texts) > 1:
        print(f"🔄 [DEBOUNCE] Combined {len(texts)} msgs from {push_name}: '{combined_text}'")
    
    # Process as a single turn
    run_async_process(target_jid, incoming_jid, combined_text, quoted_payload, push_name)

@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    try:
        data = request.json

        # --- RAW PAYLOAD LOG (to find senderPn / participant field) ---
        logging.info(f"📦 RAW WEBHOOK PAYLOAD: {json.dumps(data, indent=2, default=str)}")

        # ── PARSE & VALIDATE via whatsapp_payload ──
        msg = parse_webhook_payload(data)
        if msg is None:
            return jsonify({'status': 'ignored'}), 200

        # ── DEDUPLICATION (LRU-based) ──
        if _is_duplicate(msg.message_id):
            return jsonify({'status': 'ignored_duplicate'}), 200

        incoming_jid = msg.remote_jid
        push_name = msg.push_name

        # --- RESOLVE IDENTITY FIRST ---
        target_jid = evolution.resolve_target_jid(incoming_jid, push_name)

        # LOG RESOLUTION
        if target_jid:
            logging.info(f"🔍 Identity Resolution: {incoming_jid} (PushName: {push_name}) -> {target_jid}")
            print(f"🔍 Identity Resolution: {incoming_jid} -> {target_jid}")
        else:
            logging.warning(f"⚠️ Identity Resolution FAILED for: {incoming_jid} (PushName: {push_name})")

        # LOG CONTACT FOR DASHBOARD
        logging.info("📊 Logging contact...")
        if target_jid and "@s.whatsapp.net" in target_jid:
             try:
                 log_contact("whatsapp", target_jid.split('@')[0], push_name)
                 logging.info("✅ Contact logged.")
             except Exception as e:
                 logging.error(f"⚠️ Contact log failed: {e}")

        # --- LEARNING MODE: OWNER SETUP CHECK ---
        logging.info("👑 Checking owner status...")
        try:
            is_owner = (target_jid == Config.OWNER_PHONE_NUMBER)
            logging.info(f"👑 is_owner: {is_owner}")
        except Exception as e:
            logging.error(f"❌ Owner check failed: {e}")
            is_owner = False

        global pending_lid_resolutions

        # Use NormalizedMessage fields
        text_content = msg.text_content
        media_type = msg.media_type
        media_msg = msg.media_message
        message_id = msg.message_id

        logging.info(f"ℹ️ Types determined: Media={media_type}, TextLen={len(text_content)}")

        # --- OWNER COMMANDS ---
        if is_owner and text_content:
            logging.info("👤 Owner text check...")
            import re
            clean_text = re.sub(r'\D', '', text_content)

            if 10 <= len(clean_text) <= 15 and pending_lid_resolutions:
                real_number = clean_text + "@s.whatsapp.net"
                last_lid, last_name = list(pending_lid_resolutions.items())[-1]

                db.save_lid_mapping(last_lid, real_number, last_name)
                del pending_lid_resolutions[last_lid]

                evolution.send_text(Config.OWNER_PHONE_NUMBER, f"✅ Linked '{last_name}' to {real_number}. You can now reply.")
                return jsonify({'status': 'linked_manual'}), 200

        # --- UNKNOWN LID CHECK ---
        if not target_jid:
            logging.info("❓ Unknown LID check...")
            if incoming_jid not in pending_lid_resolutions:
                 pending_lid_resolutions[incoming_jid] = push_name
                 evolution.send_text(Config.OWNER_PHONE_NUMBER,
                     f"⚠️ Unknown contact '{push_name}' (LID). I can't find their number.\n\nReply with their phone number (e.g. 919876543210) to link them.")
                 print(f"⚠️ Alerted owner about unknown LID: {incoming_jid}")

            return jsonify({'status': 'waiting_for_manual_link'}), 200

        # Prepare Quote (using normalised helper)
        quoted_payload = build_quoted_payload(msg)

        # ------------------------------------------------------------------
        # PROCESS MESSAGE (Text vs Media)
        # ------------------------------------------------------------------

        if media_type == 'document' and media_msg:
            print(f"📄 Received DOCUMENT from {push_name}")
            logging.info(f"📄 Received DOCUMENT from {push_name}")
            try:
                def process_document_thread():
                    try:
                        from data_analyst import ingest_file
                        filename = media_msg.get('fileName', 'document')
                        ext = os.path.splitext(filename)[1].lower()

                        if ext not in ('.csv', '.xlsx', '.xls', '.pdf', '.txt', '.md'):
                            evolution.send_text(target_jid, f"⚠️ Unsupported file type: {ext}. Supported: CSV, Excel, PDF, TXT", quoted_payload)
                            return

                        if not os.path.exists(Config.TEMP_DIR):
                            os.makedirs(Config.TEMP_DIR)
                        temp_path = os.path.join(Config.TEMP_DIR, f"doc_{message_id}_{filename}")

                        fetched_b64 = evolution.get_base64_from_message(message_id)
                        if fetched_b64:
                            import base64 as b64mod
                            with open(temp_path, 'wb') as f:
                                f.write(b64mod.b64decode(fetched_b64))
                        else:
                            evolution.send_text(target_jid, "⚠️ Could not download the document. Please try again.", quoted_payload)
                            return

                        result = ingest_file(temp_path, filename)
                        os.unlink(temp_path)

                        if 'error' in result:
                            evolution.send_text(target_jid, f"❌ {result['error']}", quoted_payload)
                        else:
                            summary = result.get('summary', '✅ Document loaded!')
                            summary += f"\n\n💬 Ab koi bhi sawaal poochiye is data ke baare mein!"
                            evolution.send_text(target_jid, summary, quoted_payload)
                    except Exception as e:
                        logging.error(f"❌ Document process error: {e}")
                        traceback.print_exc()

                thread = threading.Thread(target=process_document_thread)
                thread.start()
            except Exception as e:
                logging.error(f"❌ Failed to start document thread: {e}")

        elif media_type in ('audio', 'image') and media_msg and handler:
            print(f"📥 Received {media_type.upper()} from {push_name}")
            logging.info(f"📥 Received {media_type.upper()} from {push_name}")
            try:
                logging.info(f"🚀 Starting THREAD for {media_type}...")
                thread = threading.Thread(
                    target=run_async_media_process,
                    args=(target_jid, incoming_jid, media_type, media_msg, quoted_payload, message_id, push_name)
                )
                thread.start()
                logging.info(f"✅ THREAD started!")
            except Exception as e:
                logging.error(f"❌ Failed to start media thread: {e}")

        elif text_content:
            print(f"📝 Received: '{text_content}' from {push_name}")
            logging.info(f"📝 Received: '{text_content}' from {push_name}")

            # Rate limiting
            if _is_rate_limited(target_jid):
                logging.warning(f"⚠️ Rate limited: {push_name} ({target_jid})")
                return jsonify({'status': 'rate_limited'}), 200

            db.save_message(platform='whatsapp', user_id=target_jid, user_name=push_name, message_text=text_content, direction='incoming')

            if handler:
                _debounce_and_process(target_jid, incoming_jid, text_content, quoted_payload, push_name)
            else:
                logging.error("❌ Handler is None! Cannot process message.")

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        print(f"❌ Webhook Crash: {e}")
        logging.error(f"❌ Webhook Crash: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error'}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=Config.WEBHOOK_PORT)