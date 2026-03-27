from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import requests
import os
from threading import Thread
from database import db
from config import Config
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

from contacts_manager import log_contact, update_contact_card
from message_scheduler import (
    get_all_schedules, add_schedule, update_schedule,
    delete_schedule, toggle_schedule, get_schedule_log,
    start_scheduler_background
)
from data_analyst import (
    ingest_file, query_data, list_documents,
    delete_document as analyst_delete_document
)
from daily_context import get_daily_context, set_daily_context

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

class UnifiedBotManager:
    def __init__(self):
        self.evolution_api = {
            'base_url': Config.EVOLUTION_API_URL,
            'api_key': Config.EVOLUTION_API_KEY,
            'instance': Config.EVOLUTION_INSTANCE_NAME
        }
    
    def send_whatsapp_message(self, phone_number, message, message_type='text', audio_path=None):
        log_contact('whatsapp', phone_number, f"User {phone_number}")
        headers = {'apikey': self.evolution_api['api_key'], 'Content-Type': 'application/json'}
        response_data = {}
        
        try:
            if message_type == 'text':
                url = f"{self.evolution_api['base_url']}/message/sendText/{self.evolution_api['instance']}"
                payload = {"number": phone_number, "text": message}
                response = requests.post(url, json=payload, headers=headers, timeout=10)
                response_data = response.json()
            elif message_type == 'audio' and audio_path:
                url = f"{self.evolution_api['base_url']}/message/sendMedia/{self.evolution_api['instance']}"
                import base64
                with open(audio_path, 'rb') as audio_file:
                    audio_base64 = base64.b64encode(audio_file.read()).decode('utf-8')
                payload = {"number": phone_number, "mediatype": "audio", "media": audio_base64, "caption": message}
                response = requests.post(url, json=payload, headers=headers)
                response_data = response.json()

            db.save_message(
                platform='whatsapp', user_id=phone_number, user_name=phone_number,
                message_text=message, direction='outgoing',
                message_type=message_type, audio_path=audio_path
            )
            return response_data
        except Exception as e:
            return {'error': str(e)}

    def send_telegram_message(self, chat_id, message, message_type='text', audio_path=None):
        log_contact('telegram', chat_id, f"User {chat_id}")
        token = Config.TELEGRAM_BOT_TOKEN
        try:
            if message_type == 'text':
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {"chat_id": chat_id, "text": message}
                requests.post(url, json=payload, timeout=10)
            elif message_type == 'audio' and audio_path:
                url = f"https://api.telegram.org/bot{token}/sendVoice"
                with open(audio_path, 'rb') as audio:
                    requests.post(url, data={"chat_id": chat_id}, files={"voice": audio}, timeout=30)

            db.save_message(
                platform='telegram', user_id=str(chat_id), user_name=f"User_{chat_id}",
                message_text=message, direction='outgoing',
                message_type=message_type, audio_path=audio_path
            )
            return {'status': 'success'}
        except Exception as e:
            print(f"❌ Telegram Send Error: {e}")
            return {'status': 'error', 'message': str(e)}

bot_manager = UnifiedBotManager()

# ─── EXISTING API ENDPOINTS ─────────────────────────────────────────────────

@app.route('/api/messages', methods=['GET'])
def get_messages():
    platform = request.args.get('platform')
    limit = int(request.args.get('limit', 100))
    messages = db.get_recent_messages(limit=limit, platform=platform)
    for msg in messages:
        if msg.get('timestamp'):
            ts = msg['timestamp']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            msg['timestamp'] = ts.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    return jsonify(messages)

@app.route('/api/users', methods=['GET'])
def get_users():
    users = db.get_active_users()
    for user in users:
        if user.get('last_message'):
            ts = user['last_message']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            user['last_message'] = ts.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    return jsonify(users)

@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.json
    platform = data.get('platform')
    user_id = data.get('user_id')
    message = data.get('message')
    
    if platform == 'whatsapp':
        result = bot_manager.send_whatsapp_message(user_id, message)
    elif platform == 'telegram':
        result = bot_manager.send_telegram_message(user_id, message)
    else:
        return jsonify({'error': 'Invalid platform'}), 400
    return jsonify(result)

@app.route('/api/stats', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    """Comprehensive health check — probes all backend services."""
    import time as _t
    checks = {}

    # 1. PostgreSQL
    try:
        t0 = _t.time()
        db._ensure_connection()
        checks["postgresql"] = {"status": "healthy", "latency_ms": round((_t.time() - t0) * 1000, 1)}
    except Exception as e:
        checks["postgresql"] = {"status": "unhealthy", "error": str(e)}

    # 2. Ollama LLM
    try:
        t0 = _t.time()
        import ollama as _ollama
        _ollama.list()
        checks["ollama"] = {"status": "healthy", "latency_ms": round((_t.time() - t0) * 1000, 1)}
    except Exception as e:
        checks["ollama"] = {"status": "unhealthy", "error": str(e)}

    # 3. Evolution API
    try:
        t0 = _t.time()
        r = requests.get(
            f"{Config.EVOLUTION_API_URL}/instance/connectionState/{Config.EVOLUTION_INSTANCE_NAME}",
            headers={"apikey": Config.EVOLUTION_API_KEY},
            timeout=5,
        )
        checks["evolution_api"] = {
            "status": "healthy" if r.status_code == 200 else "degraded",
            "http_code": r.status_code,
            "latency_ms": round((_t.time() - t0) * 1000, 1),
        }
    except Exception as e:
        checks["evolution_api"] = {"status": "unhealthy", "error": str(e)}

    # 4. ChromaDB (vector store)
    try:
        t0 = _t.time()
        import chromadb as _chroma
        client = _chroma.PersistentClient(path=os.path.join(os.path.dirname(__file__), "chroma_db"))
        collections = client.list_collections()
        checks["chromadb"] = {
            "status": "healthy",
            "collections": len(collections),
            "latency_ms": round((_t.time() - t0) * 1000, 1),
        }
    except Exception as e:
        checks["chromadb"] = {"status": "unhealthy", "error": str(e)}

    overall = "healthy" if all(c.get("status") == "healthy" for c in checks.values()) else "degraded"
    return jsonify({"status": overall, "services": checks})

# ─── SCHEDULER API ENDPOINTS ────────────────────────────────────────────────

@app.route('/api/schedules', methods=['GET'])
def api_get_schedules():
    return jsonify(get_all_schedules())

@app.route('/api/schedules', methods=['POST'])
def api_create_schedule():
    data = request.json
    schedule = add_schedule(
        contact_key=data.get("contact_key", ""),
        contact_name=data.get("contact_name", ""),
        platform=data.get("platform", "whatsapp"),
        phone_or_id=data.get("phone_or_id", ""),
        message=data.get("message", ""),
        time_str=data.get("time", "09:00"),
        schedule_type=data.get("schedule_type", "daily"),
        days_of_week=data.get("days_of_week", []),
        one_time_date=data.get("one_time_date", ""),
    )
    return jsonify(schedule), 201

@app.route('/api/schedules/<schedule_id>', methods=['PUT'])
def api_update_schedule(schedule_id):
    data = request.json
    if data.get("toggle"):
        new_state = toggle_schedule(schedule_id)
        if new_state is not None:
            return jsonify({"enabled": new_state})
        return jsonify({"error": "Not found"}), 404
    
    success = update_schedule(schedule_id, data)
    if success:
        return jsonify({"status": "updated"})
    return jsonify({"error": "Not found"}), 404

@app.route('/api/schedules/<schedule_id>', methods=['DELETE'])
def api_delete_schedule(schedule_id):
    success = delete_schedule(schedule_id)
    if success:
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Not found"}), 404

@app.route('/api/schedules/log', methods=['GET'])
def api_schedule_log():
    return jsonify(get_schedule_log())

# ─── ANALYST API ENDPOINTS ──────────────────────────────────────────────────

@app.route('/api/analyst/upload', methods=['POST'])
def api_analyst_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No filename'}), 400
    
    # Save temp file
    import tempfile
    ext = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name
    
    result = ingest_file(tmp_path, file.filename)
    os.unlink(tmp_path)  # Clean up temp
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result), 201

@app.route('/api/analyst/query', methods=['POST'])
def api_analyst_query():
    data = request.json
    question = data.get('question', '')
    doc_id = data.get('doc_id')
    if not question:
        return jsonify({'error': 'No question provided'}), 400
    result = query_data(question, doc_id)
    return jsonify(result)

@app.route('/api/analyst/documents', methods=['GET'])
def api_analyst_documents():
    return jsonify(list_documents())

@app.route('/api/analyst/documents/<doc_id>', methods=['DELETE'])
def api_analyst_delete_doc(doc_id):
    success = analyst_delete_document(doc_id)
    if success:
        return jsonify({'status': 'deleted'})
    return jsonify({'error': 'Not found'}), 404

# ─── DAILY CONTEXT API ENDPOINTS ────────────────────────────────────────────

@app.route('/api/daily-context', methods=['GET'])
def api_get_daily_context():
    return jsonify(get_daily_context())

@app.route('/api/daily-context', methods=['POST'])
def api_set_daily_context():
    data = request.json
    result = set_daily_context(
        mood=data.get("mood", ""),
        activity=data.get("activity", ""),
        notable=data.get("notable", ""),
    )
    return jsonify(result)

# ─── CONTACT CARD API ENDPOINT ──────────────────────────────────────────────

@app.route('/api/contacts/<contact_key>/card', methods=['PUT'])
def api_update_contact_card(contact_key):
    data = request.json
    success = update_contact_card(
        contact_key=contact_key,
        nickname=data.get("nickname"),
        shared_context=data.get("shared_context"),
        topics=data.get("topics"),
    )
    if success:
        return jsonify({"status": "updated"})
    return jsonify({"error": "Contact not found"}), 404


if __name__ == '__main__':
    print("🌐 Unified API Server Running...")
    start_scheduler_background()
    socketio.run(app, host='0.0.0.0', port=5001)