# Voice2Claude

A local voice-narrated AI chat application. Type or dictate messages and hear responses read aloud in real time, sentence by sentence, powered by Claude (Anthropic) and ElevenLabs.

Runs entirely on your own machine at `http://localhost:8000` — no third party hosts your conversations or API keys.

## Features

- **Streaming voice responses** — ElevenLabs TTS plays each sentence as it arrives, minimising latency
- **Conversation management** — sidebar with named conversations grouped by Today / Yesterday / Older; create, rename (double-click), and delete chats
- **Persistent memory** — Claude can save facts across sessions; view and manage memories via the Memory panel
- **Web search** — Claude can search the web for time-sensitive questions
- **Playback speed** — 1× / 1.25× / 1.5× / 2× controls in the header
- **Voice picker** — choose between four built-in ElevenLabs preset voices, or set any ElevenLabs voice ID via `.env`
- **Typeless mode** — click the mic button to auto-send after a 1.5 s typing pause
- **Mute / Stop** — silence audio or cancel a response mid-stream

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| AI | Anthropic Claude (`claude-sonnet-4-6` default) |
| TTS | ElevenLabs REST API (streaming) |
| Search | `ddgs` (DuckDuckGo) |
| Frontend | Single-file HTML/CSS/JS SPA |
| Persistence | JSON files under `data/` |

## Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- An [ElevenLabs API key](https://elevenlabs.io/)

## Setup

```powershell
# 1. Clone the repo
git clone https://github.com/shuangciyyang-ux/Voice2Claude.git
cd Voice2Claude

# 2. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create a .env file (see Configuration below)
copy .env.example .env
notepad .env

# 5. Start the server
python server.py
```

On macOS / Linux:

```bash
git clone https://github.com/shuangciyyang-ux/Voice2Claude.git
cd Voice2Claude
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env
python server.py
```

Then open `http://localhost:8000` in your browser.

## Configuration

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
ELEVENLABS_API_KEY=...

# Optional overrides
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
ELEVENLABS_MODEL=eleven_turbo_v2_5
CLAUDE_MODEL=claude-sonnet-4-6
```

### Choosing a voice

The voice picker in the UI offers four well-known ElevenLabs preset voices. To use a different voice:

1. Browse the [ElevenLabs voice library](https://elevenlabs.io/app/voice-library), find one you like, and copy its **Voice ID**.
2. Either:
   - Set `ELEVENLABS_VOICE_ID` in `.env` to override the default, **or**
   - Edit the voice list in `index.html` (look for `vp-row` entries) to add/replace voices in the picker.

## Project Structure

```
Voice2Claude/
├── server.py          # FastAPI backend — chat, TTS, search, memory, conversation CRUD
├── app.py             # Desktop launcher (starts server + opens browser)
├── index.html         # Single-file frontend SPA
├── requirements.txt
├── .env               # API keys (not committed)
├── static/            # Optional: place app.ico here for a custom desktop-shortcut icon
└── data/
    ├── conversations/  # One JSON file per conversation (auto-created)
    └── memory.json     # Persistent memory items (auto-created)
```

## Optional: Build a standalone .exe (Windows)

The repo includes a PyInstaller spec and build scripts to produce a single-file Windows executable.

```powershell
# Bake .env values into app.py so they're embedded in the .exe
.\bake_credentials.ps1

# Build the .exe (~2-5 minutes the first time)
.\build.ps1
```

The output appears at `dist\Voice2Claude.exe`. **Anyone who can run the .exe can extract the embedded API keys with simple tools** — set monthly spend caps on your API accounts before sharing it, and never commit a baked `app.py`. (A pre-commit hook in this repo blocks accidental commits of baked credentials.)

To put a launcher on your Desktop:

```powershell
.\install_shortcut.ps1
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves the frontend |
| POST | `/chat/voice` | Main chat endpoint — SSE stream of text deltas and audio chunks |
| GET | `/conversations` | List all conversations |
| POST | `/conversations` | Create a new conversation |
| GET | `/conversations/{id}` | Load a conversation's messages |
| DELETE | `/conversations/{id}` | Delete a conversation |
| PUT | `/conversations/{id}/rename` | Rename a conversation |
| GET | `/memory` | List all memory items |
| POST | `/memory` | Add a memory item |
| DELETE | `/memory/{id}` | Delete a memory item |
| POST | `/voice` | Set the active ElevenLabs voice |
| POST | `/tts/stream` | Standalone TTS SSE stream |

## SSE Event Types

The `/chat/voice` endpoint streams Server-Sent Events:

| Type | Payload | Description |
|------|---------|-------------|
| `delta` | `text` | Incremental Claude text |
| `audio` | `b64`, `seq` | Base64 MP3 audio chunk |
| `chunk_end` | `seq` | Signals a TTS sentence is complete |
| `tool_call` | `name`, `query` | Claude invoked a tool |
| `memory_saved` | `content` | Claude saved a memory |
| `error` | `error` | Backend error |
| `tts_error` | `error`, `seq` | TTS error for a specific chunk |
| `done` | `full`, `conversation_id`, `conversation_name` | Response complete |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Enter | Send message |
| Shift + Enter | New line in input |
| Escape | Close voice picker / memory modal |

## License

MIT — see `LICENSE` (add your own if you fork this).
