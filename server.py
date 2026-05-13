"""
Voice2Claude — local server
Features: streaming chat, ElevenLabs TTS, conversation history, persistent memory, web search.
"""

import os
import re
import json
import base64
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from anthropic import AsyncAnthropic
from dotenv import load_dotenv


def resource_path(*parts: str) -> Path:
    """Read-only bundled assets (HTML, static files, vendor data).
    In frozen mode this points inside PyInstaller's temp extraction dir,
    which is wiped on every exit — never write user data here."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def data_path(*parts: str) -> Path:
    """Persistent user data (conversations, memory). Lives next to the exe
    in frozen mode, or in the project dir in dev mode — survives restarts."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


if getattr(sys, "frozen", False):
    _env_path = Path(sys.executable).parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path)
    else:
        load_dotenv()
else:
    load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

if not ANTHROPIC_API_KEY:
    raise RuntimeError("Missing ANTHROPIC_API_KEY in .env")
if not ELEVENLABS_API_KEY:
    raise RuntimeError("Missing ELEVENLABS_API_KEY in .env")

app = FastAPI()
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

_static_dir = resource_path("static")
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------
DATA_DIR = data_path("data")
CONV_DIR = DATA_DIR / "conversations"
MEMORY_FILE = DATA_DIR / "memory.json"
DATA_DIR.mkdir(exist_ok=True)
CONV_DIR.mkdir(exist_ok=True)

current_voice: str = ELEVENLABS_VOICE_ID

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 5,
    },
    {
        "name": "remember_information",
        "description": (
            "Save important information to persistent memory so it can be recalled in future conversations. "
            "Use this when the user shares personal details, preferences, or explicitly asks you to remember something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The information to remember"}
            },
            "required": ["content"],
        },
    },
]


# ---------------------------------------------------------------------------
# Conversation storage
# ---------------------------------------------------------------------------

def _conv_path(conv_id: str) -> Path:
    return CONV_DIR / f"{conv_id}.json"


def _load_conv(conv_id: str) -> dict:
    p = _conv_path(conv_id)
    if not p.exists():
        raise HTTPException(404, f"Conversation {conv_id} not found")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_conv(conv: dict):
    _conv_path(conv["id"]).write_text(
        json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _list_convs() -> list[dict]:
    result = []
    for p in CONV_DIR.glob("*.json"):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            msgs = c.get("messages", [])
            preview = next(
                (m["content"][:100] for m in msgs
                 if m["role"] == "assistant" and isinstance(m.get("content"), str)),
                "",
            )
            result.append({
                "id": c["id"],
                "name": c["name"],
                "created_at": c["created_at"],
                "updated_at": c.get("updated_at", c["created_at"]),
                "preview": preview,
                "message_count": len(msgs),
            })
        except Exception:
            pass
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result


def _new_conv() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    conv = {
        "id": str(uuid.uuid4()),
        "name": "New conversation",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    _save_conv(conv)
    return conv


# ---------------------------------------------------------------------------
# Memory storage
# ---------------------------------------------------------------------------

def _load_memory() -> list[dict]:
    if not MEMORY_FILE.exists():
        return []
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []


def _save_memory(items: list[dict]):
    MEMORY_FILE.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _add_memory(content: str) -> dict:
    items = _load_memory()
    item = {
        "id": str(uuid.uuid4()),
        "content": content.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(item)
    _save_memory(items)
    return item


async def _generate_title(messages: list[dict]) -> str:
    """Generate a concise 3-6 word title from the first exchange (Claude.ai-style)."""
    parts = []
    for m in messages[:4]:
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        role = "User" if m["role"] == "user" else "Assistant"
        parts.append(f"{role}: {content[:500]}")
    convo = "\n".join(parts)
    try:
        resp = await claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    "Generate a short 3-6 word title for this conversation. "
                    "Reply with ONLY the title — no quotes, no period, no prefix.\n\n"
                    f"{convo}"
                ),
            }],
        )
        title = "".join(b.text for b in resp.content if b.type == "text").strip()
        title = title.strip('"\'').strip()
        return title[:60]
    except Exception:
        return ""


def _build_system_prompt() -> str:
    items = _load_memory()
    now = datetime.now()
    today = now.strftime("%A, %B %d, %Y")
    base = (
        f"You are a helpful AI assistant with access to web_search and persistent memory tools.\n"
        f"Today's date is {today}. Your training cutoff is January 2026 — anything that has "
        f"happened or could have changed since then is OUTSIDE your knowledge.\n\n"
        f"=== MANDATORY TOOL-USE RULES ===\n"
        f"You MUST call the web_search tool BEFORE answering whenever the user asks about ANY of:\n"
        f"  - news, current events, latest/recent anything\n"
        f"  - prices, stocks, exchange rates, sports scores\n"
        f"  - weather, traffic, schedules\n"
        f"  - product releases, software versions, company updates\n"
        f"  - anything dated 'today', 'this week', 'this month', '{now.year}', or 'now'\n"
        f"  - any specific fact you are not 100% certain about\n\n"
        f"HARD PROHIBITIONS:\n"
        f"  - NEVER write phrases like 'According to my search', 'Based on the latest data', "
        f"'I just looked up', 'Recent reports show', or any similar wording UNLESS you have "
        f"ACTUALLY called the web_search tool in this turn. Fabricating a search is the worst "
        f"possible failure mode.\n"
        f"  - NEVER answer time-sensitive questions from training data. If a search would help "
        f"and you skip it, you are wrong even if your answer happens to be right.\n"
        f"  - NEVER invent URLs or citations. Citations come automatically from real searches.\n\n"
        f"WHEN YOU SEARCH:\n"
        f"  - Include the current year ({now.year}) in your query string.\n"
        f"  - If unsure whether to search, search anyway. The cost of an extra search is small; "
        f"the cost of confidently wrong stale info is large.\n"
        f"  - If the FIRST search returns no useful info, try DIFFERENT queries (different keywords, "
        f"site:domain.com filters, the local-language name of the target — e.g. '天文台' for HKO). "
        f"You have up to 5 searches per turn. Use them.\n\n"
        f"WHEN A SEARCH RETURNS NO USEFUL DATA (after retrying):\n"
        f"  - State plainly: 'I searched but couldn't get current data for X.'\n"
        f"  - Offer the official source URL if you know one (e.g. www.hko.gov.hk for HK weather).\n"
        f"  - STOP. Do NOT pad the answer with monthly outlooks, seasonal averages, climate norms, "
        f"or any other training-data guess. 'I don't have it' is the CORRECT answer when search fails.\n"
        f"  - Specifically forbidden: writing 'Hong Kong is expecting normal-to-above-normal "
        f"temperatures' or similar generic forecast sentences when you have no actual current data.\n\n"
        f"Use the remember_information tool to save things the user wants recalled across sessions."
    )
    if not items:
        return base
    mem_lines = "\n".join(f"- {i['content']}" for i in items)
    return f"{base}\n\nMemories from previous conversations:\n{mem_lines}"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    voice_id: str | None = None
    conversation_id: str | None = None


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None


class VoiceRequest(BaseModel):
    voice_id: str


class RenameRequest(BaseModel):
    name: str


class MemoryAddRequest(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Routes: static + root
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return FileResponse(resource_path("index.html"))


# ---------------------------------------------------------------------------
# Conversation endpoints
# ---------------------------------------------------------------------------

@app.get("/conversations")
async def get_conversations():
    return {"conversations": _list_convs()}


@app.post("/conversations")
async def create_conversation():
    c = _new_conv()
    return {"id": c["id"], "name": c["name"], "created_at": c["created_at"], "updated_at": c["updated_at"]}


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = _load_conv(conv_id)
    display = [
        {"role": m["role"], "content": m["content"]}
        for m in conv.get("messages", [])
        if isinstance(m.get("content"), str) and m["content"].strip()
    ]
    return {"id": conv["id"], "name": conv["name"], "messages": display}


@app.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    p = _conv_path(conv_id)
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.put("/conversations/{conv_id}/rename")
async def rename_conversation(conv_id: str, req: RenameRequest):
    conv = _load_conv(conv_id)
    conv["name"] = req.name.strip() or "Untitled"
    _save_conv(conv)
    return {"ok": True, "name": conv["name"]}


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------

@app.get("/memory")
async def get_memory():
    return {"items": _load_memory()}


@app.post("/memory")
async def add_memory(req: MemoryAddRequest):
    return _add_memory(req.content)


@app.delete("/memory/{item_id}")
async def delete_memory(item_id: str):
    _save_memory([i for i in _load_memory() if i["id"] != item_id])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------

@app.post("/voice")
async def set_voice(req: VoiceRequest):
    global current_voice
    if not req.voice_id.strip():
        raise HTTPException(400, "empty voice_id")
    current_voice = req.voice_id.strip()
    return {"ok": True, "voice_id": current_voice}


# ---------------------------------------------------------------------------
# Legacy compat
# ---------------------------------------------------------------------------

@app.post("/reset")
async def reset():
    return {"ok": True}


@app.get("/history")
async def history():
    return {"messages": []}


# ---------------------------------------------------------------------------
# Sentence chunking
# ---------------------------------------------------------------------------
SENTENCE_END = re.compile(r'([.!?])(\s+|$)|([\n;:])')


def extract_chunks(buffer: str, is_first: bool) -> tuple[list[str], str, bool]:
    chunks: list[str] = []
    min_len = 20 if is_first else 60
    last_cut = 0
    for m in SENTENCE_END.finditer(buffer):
        end = m.end()
        candidate = buffer[last_cut:end].strip()
        if len(candidate) >= min_len:
            chunks.append(candidate)
            last_cut = end
            min_len = 60
            is_first = False
    return chunks, buffer[last_cut:], is_first


# ---------------------------------------------------------------------------
# ElevenLabs streaming TTS
# ---------------------------------------------------------------------------

async def stream_tts(text: str, voice_id: str) -> AsyncIterator[bytes]:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        "optimize_streaming_latency": 3,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", url,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
        ) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise RuntimeError(f"ElevenLabs {r.status_code}: {body[:300]!r}")
            async for raw in r.aiter_bytes(chunk_size=4096):
                if raw:
                    yield raw


# ---------------------------------------------------------------------------
# Combined chat + voice endpoint
# ---------------------------------------------------------------------------

@app.post("/chat/voice")
async def chat_voice(req: ChatRequest):
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "empty message")

    # Snapshot voice for THIS request only — don't mutate the global, so two
    # devices using different voices won't interfere with each other.
    voice = req.voice_id or current_voice

    # Load or create conversation
    if req.conversation_id:
        try:
            conv = _load_conv(req.conversation_id)
        except HTTPException:
            conv = _new_conv()
    else:
        conv = _new_conv()

    conv["messages"].append({"role": "user", "content": user_msg})

    async def generator() -> AsyncIterator[str]:
        full_text = ""
        text_buffer = ""
        is_first_chunk = True
        seq_counter = 0

        tts_queue: asyncio.Queue = asyncio.Queue()
        out_queue: asyncio.Queue = asyncio.Queue()
        producer_done = asyncio.Event()

        async def tts_worker():
            while True:
                item = await tts_queue.get()
                if item is None:
                    return
                seq, chunk_text = item
                try:
                    async for audio_bytes in stream_tts(chunk_text, voice):
                        b64 = base64.b64encode(audio_bytes).decode("ascii")
                        await out_queue.put({"type": "audio", "b64": b64, "seq": seq})
                    await out_queue.put({"type": "chunk_end", "seq": seq})
                except Exception as e:
                    await out_queue.put({"type": "tts_error", "error": str(e), "seq": seq})

        async def claude_worker():
            nonlocal full_text, text_buffer, is_first_chunk, seq_counter
            saved = False

            # Text-only message history is valid for Claude
            working = [
                {"role": m["role"], "content": m["content"]}
                for m in conv["messages"]
            ]

            # Accumulate citations across all loop iterations for the final footer
            citations: dict[str, str] = {}  # url -> title

            try:
                # Agentic loop — handles tool use transparently
                while True:
                    async with claude.messages.stream(
                        model=CLAUDE_MODEL,
                        max_tokens=4096,
                        messages=working,
                        tools=TOOLS,
                        system=_build_system_prompt(),
                    ) as stream:
                        async for delta in stream.text_stream:
                            full_text += delta
                            text_buffer += delta
                            await out_queue.put({"type": "delta", "text": delta})
                            chunks, text_buffer, is_first_chunk = extract_chunks(
                                text_buffer, is_first_chunk
                            )
                            for c in chunks:
                                seq_counter += 1
                                await tts_queue.put((seq_counter, c))
                        final_msg = await stream.get_final_message()

                    # Surface server-side web searches to the UI and harvest citations
                    for blk in final_msg.content:
                        if blk.type == "server_tool_use" and blk.name == "web_search":
                            await out_queue.put({
                                "type": "tool_call",
                                "name": "web_search",
                                "query": (blk.input or {}).get("query", ""),
                            })
                        elif blk.type == "text":
                            for cite in (getattr(blk, "citations", None) or []):
                                url = getattr(cite, "url", None)
                                if url and url not in citations:
                                    citations[url] = getattr(cite, "title", "") or url

                    if final_msg.stop_reason not in ("tool_use", "pause_turn"):
                        break

                    # Serialize assistant turn for working history. Server-side blocks
                    # (server_tool_use, web_search_tool_result) MUST be passed back
                    # verbatim — their encrypted fields are required for citations
                    # and pause_turn continuation.
                    asst_content = []
                    for blk in final_msg.content:
                        if blk.type == "text":
                            entry = {"type": "text", "text": blk.text}
                            cites = getattr(blk, "citations", None)
                            if cites:
                                entry["citations"] = [c.model_dump() for c in cites]
                            asst_content.append(entry)
                        elif blk.type == "tool_use":
                            asst_content.append({
                                "type": "tool_use",
                                "id": blk.id,
                                "name": blk.name,
                                "input": blk.input,
                            })
                        elif blk.type in ("server_tool_use", "web_search_tool_result"):
                            asst_content.append(blk.model_dump())
                    working.append({"role": "assistant", "content": asst_content})

                    # On pause_turn there are no client tools to run — just re-issue.
                    if final_msg.stop_reason == "pause_turn":
                        continue

                    # Execute client-side tools (remember_information is the only one).
                    tool_results = []
                    for blk in final_msg.content:
                        if blk.type != "tool_use":
                            continue
                        if blk.name == "remember_information":
                            content = blk.input.get("content", "")
                            _add_memory(content)
                            await out_queue.put({"type": "memory_saved", "content": content})
                            result = f"Saved to memory: {content}"
                        else:
                            result = "Unknown tool"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": blk.id,
                            "content": result,
                        })
                    working.append({"role": "user", "content": tool_results})

                # Flush remaining text buffer to TTS
                tail = text_buffer.strip()
                if tail:
                    seq_counter += 1
                    await tts_queue.put((seq_counter, tail))

                # Append a sources footer when web search produced citations
                if citations:
                    footer = "\n\nSources:\n" + "\n".join(
                        f"- [{title}]({url})" for url, title in citations.items()
                    )
                    full_text += footer
                    await out_queue.put({"type": "delta", "text": footer})

                # Persist assistant turn (text only)
                conv["messages"].append({"role": "assistant", "content": full_text})
                conv["updated_at"] = datetime.now(timezone.utc).isoformat()
                if conv["name"] == "New conversation":
                    title = await _generate_title(conv["messages"])
                    if not title:
                        first_user = next(
                            (m["content"] for m in conv["messages"] if m["role"] == "user"), ""
                        )
                        title = first_user[:50].strip() or "New conversation"
                    conv["name"] = title
                _save_conv(conv)
                saved = True

            except asyncio.CancelledError:
                if full_text and not saved:
                    conv["messages"].append({"role": "assistant", "content": full_text})
                    conv["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if conv["name"] == "New conversation":
                        first_user = next(
                            (m["content"] for m in conv["messages"] if m["role"] == "user"), ""
                        )
                        conv["name"] = first_user[:50].strip() or "New conversation"
                    _save_conv(conv)
                raise
            except Exception as e:
                if conv["messages"] and conv["messages"][-1]["role"] == "user":
                    conv["messages"].pop()
                await out_queue.put({"type": "error", "error": str(e)})
            finally:
                if not saved and conv["messages"] and conv["messages"][-1]["role"] == "user":
                    conv["messages"].pop()
                await tts_queue.put(None)

        tts_task = asyncio.create_task(tts_worker())
        claude_task = asyncio.create_task(claude_worker())

        async def watch_done():
            await claude_task
            await tts_task
            await out_queue.put({
                "type": "done",
                "full": full_text,
                "conversation_id": conv["id"],
                "conversation_name": conv["name"],
            })
            producer_done.set()

        watcher = asyncio.create_task(watch_done())

        try:
            while True:
                if producer_done.is_set() and out_queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(out_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
        finally:
            for t in (claude_task, tts_task, watcher):
                if not t.done():
                    t.cancel()

    return StreamingResponse(generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# TTS endpoints
# ---------------------------------------------------------------------------

@app.post("/tts")
async def tts(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    voice_id = req.voice_id or ELEVENLABS_VOICE_ID

    async def gen():
        try:
            async for raw in stream_tts(text, voice_id):
                yield raw
        except RuntimeError:
            return

    return StreamingResponse(gen(), media_type="audio/mpeg")


@app.post("/tts/stream")
async def tts_stream(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    voice_id = req.voice_id or current_voice

    chunks: list[str] = []
    buf, is_first = text, True
    while True:
        new_chunks, buf, is_first = extract_chunks(buf, is_first)
        if not new_chunks:
            break
        chunks.extend(new_chunks)
    if buf.strip():
        chunks.append(buf.strip())
    if not chunks:
        chunks = [text]

    async def gen():
        seq = 0
        try:
            for chunk_text in chunks:
                seq += 1
                async for audio_bytes in stream_tts(chunk_text, voice_id):
                    b64 = base64.b64encode(audio_bytes).decode("ascii")
                    yield f"data: {json.dumps({'type':'audio','b64':b64,'seq':seq})}\n\n"
                yield f"data: {json.dumps({'type':'chunk_end','seq':seq})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','error':str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    print("\n  Voice2Claude running at http://localhost:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
