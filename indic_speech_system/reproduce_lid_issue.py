
import sys
import os

# Create relative import path to allow importing from parent directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from indic_speech_system.config import Config
from indic_speech_system.whatsapp_evolution import EvolutionAPI

def test_lid_resolution():
    print("🚀 Testing LID Resolution Logic...")
    
    # 1. Simulate the LID from the logs
    lid_jid = "101133628485854@lid"
    push_name = "User"
    
    evolution = EvolutionAPI()
    
    print(f"🎯 Attempting to resolve: {lid_jid} ({push_name})")
    
    # 2. Call the resolver
    resolved_jid = evolution.resolve_target_jid(lid_jid, push_name)
    
    print(f"\n✅ RESOLUTION RESULT: {resolved_jid}")
    
    if resolved_jid and "@s.whatsapp.net" in resolved_jid:
        print("🎉 SUCCESS: Resolved to a valid phone JID!")
    else:
        print("❌ FAILED: Could not resolve to a phone JID.")

if __name__ == "__main__":
    try:
        test_lid_resolution()
    except Exception as e:
        print(f"❌ Test Crashed: {e}")
