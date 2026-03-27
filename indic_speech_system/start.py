#!/usr/bin/env python3
from __future__ import annotations
"""
start.py — Single entry point for the Unified Bot System.

Usage:
    python start.py                # Start all services
    python start.py --no-dashboard # Headless (API + WhatsApp only)
    python start.py --check        # Health check only, don't start anything
"""
import subprocess
import sys
import os
import time
import argparse
from config import Config

# ---------------------------------------------------------------------------
# PATH RESOLUTION
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Prefer the project venv Python; fall back to whatever invoked this script.
_venv_python = os.path.join(SCRIPT_DIR, '..', '.venv', 'bin', 'python3')
PYTHON_EXE = os.path.abspath(_venv_python) if os.path.exists(_venv_python) else sys.executable


# ---------------------------------------------------------------------------
# HEALTH CHECKS
# ---------------------------------------------------------------------------
def check_service(url: str, name: str) -> bool:
    """Return True if *url* responds with any 2xx."""
    import requests
    try:
        r = requests.get(url, timeout=5)
        ok = 200 <= r.status_code < 300
        print(f"  {'✅' if ok else '❌'} {name} — HTTP {r.status_code}")
        return ok
    except Exception:
        print(f"  ❌ {name} — unreachable")
        return False


def check_evolution_api() -> bool:
    return check_service(f"{Config.EVOLUTION_API_URL}/", "Evolution API")


def check_ollama() -> bool:
    return check_service("http://localhost:11434/api/tags", "Ollama LLM")


def check_ngrok() -> str | None:
    """Detect a running ngrok tunnel and return the public HTTPS URL, or None."""
    import requests
    try:
        r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=3)
        for t in r.json().get("tunnels", []):
            pub = t.get("public_url", "")
            if pub.startswith("https://"):
                return pub
        # Fallback to first tunnel
        tunnels = r.json().get("tunnels", [])
        if tunnels:
            return tunnels[0].get("public_url")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# WEBHOOK AUTO‑CONFIG
# ---------------------------------------------------------------------------
def auto_set_webhook(ngrok_url: str) -> bool:
    """Point Evolution API webhook at the ngrok tunnel."""
    import requests
    webhook_url = f"{ngrok_url.rstrip('/')}/webhook/whatsapp"
    print(f"  🔌 Webhook target: {webhook_url}")

    evo_url = f"{Config.EVOLUTION_API_URL}/webhook/set/{Config.EVOLUTION_INSTANCE_NAME}"
    headers = {'apikey': Config.EVOLUTION_API_KEY, 'Content-Type': 'application/json'}
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT"],
        }
    }
    try:
        resp = requests.post(evo_url, json=payload, headers=headers, timeout=5)
        if resp.status_code in (200, 201):
            print("  ✅ Webhook configured!")
            return True
        else:
            print(f"  ❌ Webhook set failed: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ Webhook set error: {e}")
    return False


# ---------------------------------------------------------------------------
# SERVICE LAUNCHER
# ---------------------------------------------------------------------------
_children: list[subprocess.Popen] = []


def start_service(script_name: str, label: str) -> None:
    script = os.path.join(SCRIPT_DIR, script_name)
    proc = subprocess.Popen([PYTHON_EXE, script], cwd=SCRIPT_DIR)
    _children.append(proc)
    print(f"  🚀 {label} (PID {proc.pid})")


def shutdown_children() -> None:
    for p in _children:
        try:
            p.terminate()
        except Exception:
            pass
    print("\n👋 All services stopped.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def run_health_check() -> bool:
    """Run all health checks; return True if critical services are OK."""
    print("\n🔍 Health Check")
    print("─" * 50)
    evo_ok = check_evolution_api()
    check_ollama()
    ngrok_url = check_ngrok()
    if ngrok_url:
        print(f"  ✅ Ngrok tunnel: {ngrok_url}")
    else:
        print("  ⚠️  Ngrok not detected (start with: ngrok http 3000)")
    print("─" * 50)
    return evo_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified Bot System launcher")
    parser.add_argument("--check", action="store_true", help="Health check only")
    parser.add_argument("--no-dashboard", action="store_true", help="Skip Gradio dashboard")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 Unified Bot System")
    print("=" * 60)

    evo_ok = run_health_check()

    if args.check:
        sys.exit(0 if evo_ok else 1)

    if not evo_ok:
        print("\n❌ Evolution API is required. Start it with:")
        print("   docker-compose up -d")
        sys.exit(1)

    # --- Ngrok auto‑webhook ---
    print("\n🔗 Configuring webhook…")
    ngrok_url = check_ngrok()
    if ngrok_url and isinstance(ngrok_url, str):
        auto_set_webhook(ngrok_url)
    else:
        print("  ⚠️  Skipping webhook (no ngrok). Set WEBHOOK_URL in .env for manual config.")

    # --- Database init ---
    print("\n🗄️  Initializing database…")
    from database import db  # noqa: F401
    print("  ✅ Database ready")

    # --- Start services ---
    print("\n🚀 Starting services…")
    start_service("unified_api.py", "API Server (port 5001)")
    time.sleep(3)

    start_service("whatsapp_evolution.py", "WhatsApp Bot (port 3000)")
    time.sleep(2)

    if not args.no_dashboard:
        start_service("unified_dashboard.py", "Dashboard (port 7860)")
        time.sleep(2)

    # --- Summary ---
    print("\n" + "=" * 60)
    print("✅ All services running!")
    print("=" * 60)
    print(f"  🌐 API Server:    http://localhost:5001")
    print(f"  📱 WhatsApp Bot:  http://localhost:{Config.WEBHOOK_PORT}")
    if not args.no_dashboard:
        print(f"  📊 Dashboard:     http://localhost:7860")
    print(f"  🔗 Evolution API: {Config.EVOLUTION_API_URL}")
    print(f"  🔌 Evo Manager:   {Config.EVOLUTION_API_URL}/manager")
    print("\nPress Ctrl+C to stop all services")
    print("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_children()


if __name__ == "__main__":
    main()
