# Indic Speech System

A unified messaging bot system that supports WhatsApp and Telegram, offering Speech-to-Text (STT), AI capabilities (via Ollama), and Text-to-Speech (TTS) for Indian languages.

## 🚀 Features
- **Multi-Platform Support**: Works with WhatsApp (via Evolution API) and Telegram.
- **Unified Messaging**: Centralized database for messages from all platforms.
- **Indic Language Support**: STT and TTS optimized for Indian languages.
- **AI Integration**: Uses local Ollama (Llama 3) for intelligence.

## 🛠️ Prerequisites
- Python 3.8+
- PostgreSQL
- [Ollama](https://ollama.com/) (running locally)
- [Evolution API](https://github.com/EvolutionAPI/evolution-api) (for WhatsApp)

## 📦 Installation

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd indic_speech_system
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Setup Database**:
    - Ensure PostgreSQL is running.
    - Create a database and user (defaults: `evolution` / `evolutionpass123`).
    - The schema will be automatically created on the first run.

4.  **Configuration**:
    - Copy `.env.example` to `.env` (if not present) and fill in your details.
    
    ```ini
    # .env
    TELEGRAM_BOT_TOKEN=your_telegram_token
    
    # Evolution API
    EVOLUTION_API_URL=http://localhost:8080
    EVOLUTION_API_KEY=your_evolution_api_key
    EVOLUTION_INSTANCE_NAME=indic_speech_client
    
    # Database (Optional, defaults shown)
    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=evolution
    DB_USER=evolution
    DB_PASSWORD=evolutionpass123
    ```

## 🏃‍♂️ Running the System

Start everything with one command:

```bash
python start.py              # All services (API + WhatsApp + Dashboard)
python start.py --no-dashboard  # Headless mode (no Gradio UI)
python start.py --check      # Health check only
```

This starts:
- **API Server** → `http://localhost:5001`
- **WhatsApp Bot** → `http://localhost:3000` (webhook receiver)
- **Dashboard** → `http://localhost:7860` (Gradio UI)

Ngrok webhook is auto-configured if ngrok is running on port 4040.

## 📂 Project Structure
- `start.py`: **Single entry point** — starts all services.
- `unified_api.py`: Flask API for messages, scheduling, analytics.
- `whatsapp_evolution.py`: Webhook handler for WhatsApp via Evolution API.
- `whatsapp_payload.py`: Payload normalization and JID classification.
- `bot_handler.py`: Core AI logic, clone mode, prompt assembly.
- `prompt_loader.py`: Loads versioned prompts from `prompts/` directory.
- `prompts/`: Externalized prompt files with version metadata.
- `database.py`: Database interaction layer.
- `config.py`: Configuration management.

## 🐛 Troubleshooting
- **WhatsApp Timeout**: Ensure `ollama` is running (`ollama serve`). The bot processes AI in the background to avoid webhook timeouts.
- **Database Connection**: Check `DB_*` variables in `.env`.
- **Ngrok not detected**: Start ngrok with `ngrok http 3000` before running `start.py`.
