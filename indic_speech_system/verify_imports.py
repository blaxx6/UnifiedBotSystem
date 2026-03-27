import sys
import os

print(f"Python Executable: {sys.executable}")
print(f"PYTHONPATH: {sys.path}")

try:
    import os
    import asyncio
    import whisper
    import ollama
    print("✅ Standard & 3rd party imports successful")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

try:
    sys.path.append(os.getcwd())
    from config import Config
    print("✅ Config import successful")
except ImportError as e:
    print(f"❌ Config import failed: {e}")
    sys.exit(1)

print("ALL IMPORTS OK")
