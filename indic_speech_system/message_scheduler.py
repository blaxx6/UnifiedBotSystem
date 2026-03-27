# message_scheduler.py
"""
Scheduled Message Sending System
- JSON-based persistence (data/scheduled_messages.json)
- Supports daily, weekly, and one-time schedules
- AI-generated or custom messages
- Background async loop checks every 30 seconds
"""
import json
import os
import uuid
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
SCHEDULES_FILE = os.path.join(os.path.dirname(__file__), "data", "scheduled_messages.json")
os.makedirs(os.path.dirname(SCHEDULES_FILE), exist_ok=True)

API_BASE = "http://localhost:5001"

# ─── STORAGE ────────────────────────────────────────────────────────────────

def _load_schedules() -> list:
    """Load all schedules from disk."""
    if os.path.exists(SCHEDULES_FILE):
        try:
            with open(SCHEDULES_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_schedules(schedules: list):
    """Write schedules to disk."""
    with open(SCHEDULES_FILE, "w") as f:
        json.dump(schedules, f, indent=4, default=str)


# ─── CRUD ───────────────────────────────────────────────────────────────────

def add_schedule(
    contact_key: str,
    contact_name: str,
    platform: str,
    phone_or_id: str,
    message: str,
    time_str: str,          # "HH:MM" in 24h IST
    schedule_type: str,     # "daily" | "weekly" | "once"
    days_of_week: list = None,    # ["mon", "tue", ...] for weekly
    one_time_date: str = None,    # "YYYY-MM-DD" for once
) -> dict:
    """Create a new scheduled message."""
    schedules = _load_schedules()
    
    schedule = {
        "id": str(uuid.uuid4())[:8],
        "contact_key": contact_key,
        "contact_name": contact_name,
        "platform": platform,
        "phone_or_id": phone_or_id,
        "message": message,
        "time": time_str,
        "schedule_type": schedule_type,
        "days_of_week": days_of_week or [],
        "one_time_date": one_time_date or "",
        "enabled": True,
        "last_sent": None,
        "created_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    schedules.append(schedule)
    _save_schedules(schedules)
    print(f"📅 Schedule created: {contact_name} at {time_str} ({schedule_type})")
    return schedule


def get_all_schedules() -> list:
    """Return all schedules."""
    return _load_schedules()


def update_schedule(schedule_id: str, updates: dict) -> bool:
    """Update fields of a schedule by ID."""
    schedules = _load_schedules()
    for s in schedules:
        if s["id"] == schedule_id:
            for key, val in updates.items():
                if key in s and key != "id":
                    s[key] = val
            _save_schedules(schedules)
            return True
    return False


def toggle_schedule(schedule_id: str) -> Optional[bool]:
    """Toggle enabled/disabled. Returns new state or None if not found."""
    schedules = _load_schedules()
    for s in schedules:
        if s["id"] == schedule_id:
            s["enabled"] = not s["enabled"]
            _save_schedules(schedules)
            print(f"📅 Schedule {schedule_id} {'enabled' if s['enabled'] else 'disabled'}")
            return s["enabled"]
    return None


def delete_schedule(schedule_id: str) -> bool:
    """Delete a schedule by ID."""
    schedules = _load_schedules()
    new_schedules = [s for s in schedules if s["id"] != schedule_id]
    if len(new_schedules) < len(schedules):
        _save_schedules(new_schedules)
        print(f"🗑️ Schedule {schedule_id} deleted")
        return True
    return False


# ─── SENDING ────────────────────────────────────────────────────────────────

def _generate_ai_message(contact_name: str, platform: str) -> str:
    """Generate a natural AI check-in message for the contact."""
    try:
        from bot_handler import BotHandler
        handler = BotHandler()
        
        # Use the bot to generate a natural check-in
        prompt = f"Send a casual check-in message to {contact_name}"
        import asyncio
        loop = asyncio.new_event_loop()
        response = loop.run_until_complete(
            handler.generate_ai_response(
                user_id=f"scheduler_{contact_name}",
                text=prompt,
                user_name=contact_name
            )
        )
        loop.close()
        return response
    except Exception as e:
        print(f"⚠️ AI message generation failed: {e}")
        # Fallback messages
        import random
        fallbacks = [
            f"Hey {contact_name}! Kaise ho?",
            f"Hi {contact_name}, kya chal raha hai?",
            f"Arey {contact_name}, sab theek?",
        ]
        return random.choice(fallbacks)


def _send_scheduled_message(schedule: dict) -> bool:
    """Send a single scheduled message via the unified API."""
    try:
        message = schedule["message"]
        
        # Generate AI message if requested
        if message == "__AI_GENERATE__":
            message = _generate_ai_message(schedule["contact_name"], schedule["platform"])
            print(f"🤖 AI generated message for {schedule['contact_name']}: {message}")
        
        # Send via unified API
        payload = {
            "platform": schedule["platform"],
            "user_id": schedule["phone_or_id"],
            "message": message,
            "type": "text"
        }
        
        response = requests.post(f"{API_BASE}/api/send", json=payload, timeout=15)
        result = response.json()
        
        if response.status_code == 200 and "error" not in result:
            print(f"✅ Scheduled message sent to {schedule['contact_name']}: {message[:50]}...")
            return True
        else:
            print(f"❌ Failed to send scheduled message: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Scheduled message send error: {e}")
        return False


# ─── SCHEDULER LOOP ─────────────────────────────────────────────────────────

# Map day abbreviations to Python weekday numbers (Monday=0)
DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6
}


def _is_schedule_due(schedule: dict, now: datetime) -> bool:
    """Check if a schedule should fire right now."""
    if not schedule.get("enabled", False):
        return False
    
    # Parse scheduled time
    try:
        sched_hour, sched_min = map(int, schedule["time"].split(":"))
    except (ValueError, KeyError):
        return False
    
    # Check if current time matches (within 1-minute window)
    if now.hour != sched_hour or now.minute != sched_min:
        return False
    
    # Check if already sent in this minute
    last_sent = schedule.get("last_sent")
    if last_sent:
        try:
            last_dt = datetime.strptime(last_sent, "%Y-%m-%d %H:%M:%S")
            last_dt = last_dt.replace(tzinfo=IST)
            # If sent within last 2 minutes, skip (prevents duplicate sends)
            if (now - last_dt).total_seconds() < 120:
                return False
        except (ValueError, TypeError):
            pass
    
    stype = schedule.get("schedule_type", "daily")
    
    if stype == "daily":
        return True
    
    elif stype == "weekly":
        today_abbr = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
        return today_abbr in schedule.get("days_of_week", [])
    
    elif stype == "once":
        target_date = schedule.get("one_time_date", "")
        today_str = now.strftime("%Y-%m-%d")
        return target_date == today_str
    
    return False


async def scheduler_loop():
    """Background loop that checks and sends scheduled messages every 30 seconds."""
    print("⏰ Message Scheduler started")
    
    while True:
        try:
            now = datetime.now(IST)
            schedules = _load_schedules()
            
            for schedule in schedules:
                if _is_schedule_due(schedule, now):
                    success = _send_scheduled_message(schedule)
                    
                    if success:
                        # Update last_sent
                        update_schedule(schedule["id"], {
                            "last_sent": now.strftime("%Y-%m-%d %H:%M:%S")
                        })
                        
                        # Disable one-time schedules after sending
                        if schedule.get("schedule_type") == "once":
                            update_schedule(schedule["id"], {"enabled": False})
                            print(f"📅 One-time schedule {schedule['id']} auto-disabled after send")
                            
        except Exception as e:
            print(f"⚠️ Scheduler loop error: {e}")
        
        await asyncio.sleep(30)


def start_scheduler_background():
    """Start the scheduler in a background thread with its own event loop."""
    import threading
    
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scheduler_loop())
    
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    print("⏰ Scheduler background thread started")


# ─── SCHEDULE LOG (recent sends) ────────────────────────────────────────────

def get_schedule_log(limit: int = 20) -> list:
    """Get recent scheduled sends (schedules that have a last_sent timestamp)."""
    schedules = _load_schedules()
    sent = [s for s in schedules if s.get("last_sent")]
    sent.sort(key=lambda x: x.get("last_sent", ""), reverse=True)
    return sent[:limit]
