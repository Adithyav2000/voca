"""
Realtime bridge: Twilio Media Streams  ↔  OpenAI Realtime API.

Audio formats:
  Twilio  → mulaw 8 kHz mono (base64)
  OpenAI  → pcm16  24 kHz mono (base64)

The bridge converts between the two, forwards audio bidirectionally, handles
OpenAI tool calls by dispatching to the existing tool functions, and captures
transcript events.
"""

from __future__ import annotations

import asyncio
import audioop  # mulaw ↔ pcm conversion  (stdlib, available in CPython)
import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import structlog
import websockets
from sqlalchemy import select, update
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.database import CallTask, get_session_factory
from app.core.redis import get_redis
from app.services.orchestrator import _transition_session_status, OPENAI_BRAIN_INSTRUCTIONS
from app.services.tools import dispatch_tool_call

logger = structlog.get_logger(__name__)

# -- audit event helper -----------------------------------------------------

async def _publish_audit(session_id: str, call_task_id: str, event: str, detail: str = "") -> None:
    """Push a short audit entry to Redis so the SSE stream can relay it."""
    try:
        r = await get_redis()
        payload = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "call_task_id": call_task_id,
            "event": event,
            "detail": detail[:300],
        })
        await r.rpush(f"audit:{session_id}", payload)
    except Exception:
        pass  # best-effort; don't crash the bridge

# -- audio helpers ----------------------------------------------------------

TWILIO_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000


def _mulaw_to_pcm16_24k(payload: bytes) -> bytes:
    """Convert mulaw/8 kHz → linear PCM16/24 kHz."""
    # mulaw → pcm16 at 8 kHz
    pcm_8k = audioop.ulaw2lin(payload, 2)
    # up-sample 8 kHz → 24 kHz  (ratio 3:1)
    pcm_24k, _ = audioop.ratecv(pcm_8k, 2, 1, TWILIO_SAMPLE_RATE, OPENAI_SAMPLE_RATE, None)
    return pcm_24k


def _pcm16_24k_to_mulaw(payload: bytes) -> bytes:
    """Convert linear PCM16/24 kHz → mulaw/8 kHz."""
    # down-sample 24 kHz → 8 kHz
    pcm_8k, _ = audioop.ratecv(payload, 2, 1, OPENAI_SAMPLE_RATE, TWILIO_SAMPLE_RATE, None)
    # pcm16 → mulaw
    return audioop.lin2ulaw(pcm_8k, 2)


# -- tool schema for OpenAI Realtime session.update -------------------------

def _realtime_tool_definitions(session_id: str, call_task_id: str, user_id: str, provider_name: str = "", provider_phone: str = "") -> list[dict]:
    """Return the tools array expected by the Realtime API session.update event."""
    return [
        {
            "type": "function",
            "name": "check_availability",
            "description": "Check if a slot is available for the requested date and time. Call this when the receptionist offers a date/time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD or natural language (e.g. Friday)"},
                    "time": {"type": "string", "description": "Time in HH:MM 24h or natural language (e.g. 10 AM)"},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 30)", "default": 30},
                },
                "required": ["date", "time"],
            },
        },
        {
            "type": "function",
            "name": "report_slot_offer",
            "description": "Record the offered slot after check_availability succeeds and the slot is held.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider_name": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM 24h"},
                    "duration_minutes": {"type": "integer", "default": 30},
                    "doctor_name": {"type": "string", "description": "Doctor name if given"},
                },
                "required": ["provider_name", "date", "time"],
            },
        },
        {
            "type": "function",
            "name": "book_slot",
            "description": "Finalize booking after the receptionist confirms. Only call AFTER report_slot_offer. If this returns a calendar conflict, apologize and find a different time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM 24h"},
                    "patient_name": {"type": "string"},
                    "patient_phone": {"type": "string"},
                    "duration_min": {"type": "integer", "default": 30},
                    "doctor_name": {"type": "string"},
                    "provider_id": {"type": "string", "description": f"Always use: (empty string, will be looked up)"},
                    "provider_name": {"type": "string", "description": f"Always use: {provider_name}"},
                    "provider_phone": {"type": "string", "description": f"Always use: {provider_phone}"},
                    "session_id": {"type": "string", "description": f"Always use: {session_id}"},
                    "call_task_id": {"type": "string", "description": f"Always use: {call_task_id}"},
                    "user_id": {"type": "string", "description": f"Always use: {user_id}"},
                },
                "required": ["date", "time", "patient_name", "patient_phone"],
            },
        },
        {
            "type": "function",
            "name": "end_call",
            "description": "End the phone call. Call ONLY after booking is confirmed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["completed", "no_answer", "rejected"]},
                },
                "required": ["status"],
            },
        },
    ]


# -- main handler -----------------------------------------------------------

async def handle_twilio_media_stream(
    twilio_ws: WebSocket,
    call_task_id: str,
    *,
    session_id: str,
    user_id: str,
    provider_name: str,
    provider_phone: str,
    service_type: str,
    target_date: str | None,
    target_time: str | None,
) -> None:
    """
    Called when Twilio opens a WebSocket via <Connect><Stream>.
    Opens a parallel WebSocket to the OpenAI Realtime API and bridges audio + events.
    """
    settings = get_settings()
    log = logger.bind(call_task_id=call_task_id, session_id=session_id, event_type="realtime_bridge")

    openai_ws_url = (
        f"wss://api.openai.com/v1/realtime?model={settings.OPENAI_REALTIME_MODEL}"
    )
    openai_headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    stream_sid: str | None = None
    twilio_call_sid: str | None = None
    transcript: list[dict] = []

    async with websockets.connect(openai_ws_url, additional_headers=openai_headers) as openai_ws:
        log.info("openai_realtime_connected")
        await _publish_audit(session_id, call_task_id, "call_started", f"Connected to {provider_name}")

        # -- 1. Configure session on OpenAI side ----------------------------
        instructions = (
            OPENAI_BRAIN_INSTRUCTIONS
            + f"\n\nContext for this call:\n"
            f"- Provider: {provider_name}\n"
            f"- Provider phone: {provider_phone}\n"
            f"- Service: {service_type}\n"
            f"- Target date: {target_date or 'as soon as possible'}\n"
            f"- Target time: {target_time or 'flexible'}\n"
            f"- session_id: {session_id}\n"
            f"- call_task_id: {call_task_id}\n"
            f"- user_id: {user_id}\n"
        )

        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": instructions,
                "voice": settings.OPENAI_REALTIME_VOICE,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {"type": "server_vad"},
                "tools": _realtime_tool_definitions(session_id, call_task_id, user_id, provider_name, provider_phone),
                "tool_choice": "auto",
            },
        }))

        # -- 2. Have OpenAI greet the receptionist --------------------------
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"],
                "instructions": (
                    f"Greet the receptionist at {provider_name}. "
                    f"Say you are calling on behalf of Alex Carter to book a {service_type} appointment. "
                    f"Ask about availability for {target_date or 'the earliest date'} "
                    f"around {target_time or 'any time'}."
                ),
            },
        }))

        # -- shared flag to shut down both sides ----------------------------
        done = asyncio.Event()

        # -- 3. Twilio → OpenAI relay (audio + events) ---------------------
        async def twilio_to_openai() -> None:
            nonlocal stream_sid, twilio_call_sid
            try:
                while not done.is_set():
                    try:
                        raw = await asyncio.wait_for(twilio_ws.receive_text(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    event = msg.get("event")

                    if event == "start":
                        stream_sid = msg["start"]["streamSid"]
                        twilio_call_sid = msg["start"].get("callSid")
                        log.info("twilio_stream_started", stream_sid=stream_sid)

                    elif event == "media":
                        payload = base64.b64decode(msg["media"]["payload"])
                        pcm_24k = _mulaw_to_pcm16_24k(payload)
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm_24k).decode(),
                        }))

                    elif event == "stop":
                        log.info("twilio_stream_stopped")
                        done.set()
                        return

            except WebSocketDisconnect:
                log.info("twilio_ws_disconnected")
                done.set()
            except Exception as e:
                log.exception("twilio_to_openai_error", error=str(e))
                done.set()

        # -- 4. OpenAI → Twilio relay (audio + events) ---------------------
        async def openai_to_twilio() -> None:
            try:
                async for raw in openai_ws:
                    if done.is_set():
                        return
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    # --- audio delta → send to Twilio ----------------------
                    if msg_type == "response.audio.delta":
                        pcm_24k = base64.b64decode(msg["delta"])
                        mulaw_8k = _pcm16_24k_to_mulaw(pcm_24k)
                        if stream_sid:
                            await twilio_ws.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": base64.b64encode(mulaw_8k).decode(),
                                },
                            })

                    # --- transcript events ---------------------------------
                    elif msg_type == "response.audio_transcript.done":
                        text = msg.get("transcript", "")
                        if text:
                            transcript.append({"role": "assistant", "text": text, "ts": _now_iso()})
                            log.info("ai_transcript", text=text[:120])
                            await _publish_audit(session_id, call_task_id, "ai_said", text[:200])

                    elif msg_type == "conversation.item.input_audio_transcription.completed":
                        text = msg.get("transcript", "")
                        if text:
                            transcript.append({"role": "receptionist", "text": text, "ts": _now_iso()})
                            log.info("receptionist_transcript", text=text[:120])
                            await _publish_audit(session_id, call_task_id, "receptionist_said", text[:200])

                    # --- tool calls ----------------------------------------
                    elif msg_type == "response.function_call_arguments.done":
                        await _handle_tool_call(
                            openai_ws, msg, session_id, call_task_id, user_id,
                            provider_name, provider_phone, log,
                        )

                    # --- end-of-turn / errors ------------------------------
                    elif msg_type == "response.done":
                        # Check if the response contained an end_call tool invocation
                        pass  # handled inside _handle_tool_call

                    elif msg_type == "error":
                        log.error("openai_realtime_error", error=msg.get("error"))
                        done.set()
                        return

            except websockets.exceptions.ConnectionClosed:
                log.info("openai_ws_closed")
            except Exception as e:
                log.exception("openai_to_twilio_error", error=str(e))
            finally:
                done.set()

        # -- 5. Run both relays concurrently --------------------------------
        await asyncio.gather(twilio_to_openai(), openai_to_twilio())

    # -- 6. Clean up: persist transcript ------------------------------------
    await _persist_transcript(call_task_id, transcript, log)
    log.info("bridge_closed", transcript_turns=len(transcript))


# -- tool call handling -----------------------------------------------------

async def _handle_tool_call(
    openai_ws,
    msg: dict,
    session_id: str,
    call_task_id: str,
    user_id: str,
    provider_name: str,
    provider_phone: str,
    log,
) -> None:
    """Dispatch a tool call from the Realtime API to our tool functions and reply."""
    fn_name = msg.get("name", "")
    call_id = msg.get("call_id", "")
    try:
        args = json.loads(msg.get("arguments", "{}"))
    except json.JSONDecodeError:
        args = {}

    log.info("tool_call_received", tool=fn_name, args=args)
    await _publish_audit(session_id, call_task_id, "tool_call", f"{fn_name}({json.dumps(args)[:150]})")

    # Inject contextual IDs the AI cannot know
    args.setdefault("session_id", session_id)
    args.setdefault("call_task_id", call_task_id)
    args.setdefault("user_id", user_id)
    args.setdefault("provider_name", provider_name)
    args.setdefault("provider_phone", provider_phone)

    if fn_name == "end_call":
        # Update CallTask status and signal completion
        status = args.get("status", "completed")
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(
                update(CallTask)
                .where(CallTask.id == UUID(call_task_id))
                .values(status=status, ended_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        result_str = json.dumps({"ended": True, "status": status})
    else:
        result_str = await dispatch_tool_call(fn_name, args)

    log.info("tool_call_result", tool=fn_name, result=result_str[:200])
    await _publish_audit(session_id, call_task_id, "tool_result", f"{fn_name} → {result_str[:150]}")

    # Send result back to the Realtime API
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": result_str,
        },
    }))

    # Tell OpenAI to continue generating after the tool result
    await openai_ws.send(json.dumps({"type": "response.create"}))


# -- helpers ----------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _persist_transcript(call_task_id: str, transcript: list[dict], log) -> None:
    """Save the captured transcript to the CallTask row."""
    if not transcript:
        return
    factory = get_session_factory()
    try:
        async with factory() as db:
            await db.execute(
                update(CallTask)
                .where(CallTask.id == UUID(call_task_id))
                .values(transcript=transcript, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        log.info("transcript_persisted", turns=len(transcript))
    except Exception as e:
        log.warning("transcript_persist_failed", error=str(e))
