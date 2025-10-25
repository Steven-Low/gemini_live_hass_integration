# app/gemini.py
import asyncio
import logging
import numpy as np
import json

from google import genai
from google.genai import types

from aiortc.contrib.media import MediaStreamError
from av.audio.resampler import AudioResampler
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import (
    device_registry as dr, intent, llm, template
)

from ..models.devices import GeminiLiveDevice
from ..llm.base import BaseLLMManager
from ..services.utils import (
    get_exposed_entities,
    convert_entities_to_prompt,
    convert_openai_tools_to_gemini,
    _format_tool
)
from ..services.wakeword_client import WakeWordWSClient
from ..config.const import (
    GEMINI_SAMPLE_RATE,
    CONF_CHAT_MODEL,
    CHUNK_SIZE_BYTES,
    CHUNK_DURATION_MS,
    GEMINI_API_VERSION,
    GEMINI_VOICE,
    GEMINI_LANGUAGE,
    DOMAIN,
    LLM_TEMPLATE_PROMPT,
    WAKE_WORD_MODEL,
    WAKE_WS_URL
)


LOGGER = logging.getLogger(__name__)


WAKE_BUFFER = 400    # Multiple of 80 (Optimize accordingly with wakeword length to debounce)
WAKE_THRESHOLD = 0.6
DEBOUNCE_TIME = 2

DEFAULT_INSTRUCTIONS_PROMPT = """You are a voice assistant for Home Assistant.
Answer questions about the world truthfully.
Answer in plain text. Keep it simple and to the point.

You also have access to the following functions for smart home:
- HassTurnOn: Turns on, opens, presses, or locks a device or entity.
- HassTurnOff: Turns off, closes, disables, or unlocks a device or entity.
- HassCancelAllTimers: Cancels all timers in an area.
- HassBroadcast: Broadcasts a spoken message through the home.
- HassListAddItem: Adds an item to a to-do list. Requires "item" and "name".
- HassListCompleteItem: Marks an item as completed on a to-do list. Requires "item" and "name".
- todo_get_items: Queries a to-do list for items. Supports filtering by status (needs_action, completed, all).
- HassMediaPause: Pauses a media player (e.g., TV, speaker, receiver).
- HassMediaUnpause: Resumes playback on a media player.
- HassMediaNext: Skips a media player to the next track/item.
- HassMediaPrevious: Replays the previous track/item on a media player.
- HassSetVolume: Sets the volume percentage of a media player. Requires "volume_level" (0–100).
- GetLiveContext: Provides real-time information about the current state, value, or mode of devices, sensors, entities, or areas.

You also have access to the following additional functions or tools:
- good_bye: Function ends the current wake session when user say bye or has no intent to continue conversation.
- google_search: Tool performs a search on the internet and returns summarized results.
- code_execution: Tool executes Python code to compute results or answer questions.

When the user request requires interacting with Home Assistant, call the appropriate tool
instead of just answering in plain text. Otherwise, just answer normally.

### Examples:

User: "Turn on the living room light. (the area can be optional)"
Assistant → HassTurnOn:
{
  "name": "light",
  "area": "Living Room"
}
"""




class GeminiClientManager(BaseLLMManager):
    def __init__(self, hass:HomeAssistant, config_entry: ConfigEntry, device: GeminiLiveDevice, remote_user_id):
        super().__init__()
        self.hass = hass
        self.config_entry = config_entry
        self.device = device
        self._active = True

        self.llm_name = "gemini"
        self.session = None
        self.remote_user_id = remote_user_id
        self.tasks = []
        self.audio_playback_queue = asyncio.Queue(maxsize=10)
        self.raw_audio_to_play_queue = asyncio.Queue(maxsize=200) # increase to prevent interrupt block

        self.wake_ws_client: WakeWordWSClient | None = None
        self.session_handle = None
        self.wake_buffer = np.array([], dtype=np.int16)  # buffer for wake word detection
        self.is_wake = asyncio.Event()
        self.interrupt_enabled = True
        self.last_wake_time = 0
        self.prompt = None
        self.llm_api = None
        self.tools = []
        self.hass_function_declarations_names = []


    @callback
    def _on_wake_toggle(self):
        """Called automatically when HA switch changes."""
        asyncio.create_task(self._update_wakeword_connection())


    #TODO: Handle video frames
    async def start_video_processing(self, webrtc_track):
        asyncio.create_task(self._drain_track(webrtc_track))

    async def _drain_track(self, track):
        LOGGER.info("Skipping webrtc video tracks.")
        try:
            while True:
                await track.recv()
        except MediaStreamError:
            LOGGER.warning("Track %s:%s ended.",track.kind, track.id)
        except asyncio.CancelledError:
            pass

    async def start_session(self, webrtc_track):
        LOGGER.info(">>>>>>> Initializing Gemini Live API session <<<<<<<")

        await self._setup_llm_api()
        await self._setup_tools()
        await self._setup_prompt()
        await self._setup_wakeword()

        try:
            await self._run_gemini_loop(webrtc_track)
        except Exception as e:
            LOGGER.error("Gemini session crashed: %s", e)
        finally:
            LOGGER.warning("All gemini tasks have ended.")
            await self.stop_session()


    async def _setup_llm_api(self):
        try:
            llm_context = llm.LLMContext(
                platform=DOMAIN,
                assistant="conversation",
                context="",
                user_prompt="",
                language="",
                device_id="",
            )
            self.llm_api = await llm.async_get_api(self.hass, "assist", llm_context)
        except HomeAssistantError as err:
            LOGGER.error("Error getting LLM API: %s", err)
            self.llm_api = None
        else:
            LOGGER.info("LLM Context initialized.")

    async def _setup_tools(self):
        if not self.llm_api:
            return

        try:
            hass_tools = [
                _format_tool(tool, self.llm_api.custom_serializer)
                for tool in self.llm_api.tools or []
            ]
            function_declarations = convert_openai_tools_to_gemini(hass_tools)
            self.hass_function_declarations_names = [fc["name"] for fc in function_declarations]
            function_declarations.append({"name": "good_bye"})
            self.tools = [
                {"function_declarations": function_declarations},
                {"google_search": {}},
                {"code_execution": {}},
            ]
        except Exception as e:
            LOGGER.error("Error Setting up tools: %s", e)
        else:
            LOGGER.info("Tools initialized: %s", self.tools)

    async def _setup_prompt(self):
        parts = []
        try:
            parts.append(
                template.Template(LLM_TEMPLATE_PROMPT, self.hass).async_render(parse_result=False)
            )
            entities = await get_exposed_entities(self.hass)
            parts.append(convert_entities_to_prompt(entities))
            parts.append(llm.DEFAULT_INSTRUCTIONS_PROMPT)
            self.prompt = "\n".join(parts)

        except TemplateError as err:
            LOGGER.error("Error rendering prompt: %s", err)
        else:
            LOGGER.info("Prompt initialized: \n%s", self.prompt)

    async def _setup_wakeword(self):
        """
        Attempt to connect to a WebSocket wake-word server first (fast, separate process).
        If that fails, fall back to local openwakeword Model (in-process).
        """
        self.device.add_wake_word_enabled_listener(self._on_wake_toggle)
        asyncio.create_task(self._update_wakeword_connection())


    async def _update_wakeword_connection(self):
        """Start or stop the WS client based on the device toggle."""
        if self.device.wake_word_enabled:
            if self.wake_ws_client is None:
                LOGGER.info("Wake word enabled → starting WS client...")
                self.wake_ws_client = WakeWordWSClient(url=WAKE_WS_URL, reconnect_interval=3)
                await self.wake_ws_client.start()
            else:
                LOGGER.info("Wake word already running.")
        else:
            if self.wake_ws_client is not None:
                LOGGER.info("Wake word disabled → stopping WS client...")
                await self.wake_ws_client.stop()
                self.wake_ws_client = None


    async def _run_gemini_loop(self, webrtc_track):
        client = genai.Client(
            api_key=self.config_entry.options.get(CONF_API_KEY),
            http_options={"api_version": GEMINI_API_VERSION},
        )

        while self._active:
            gemini_config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                context_window_compression=types.ContextWindowCompressionConfig(
                    sliding_window=types.SlidingWindow(),
                ),
                session_resumption=types.SessionResumptionConfig(handle=self.session_handle),
                speech_config={
                    "voice_config": {"prebuilt_voice_config": {"voice_name": GEMINI_VOICE}},
                    "language_code": GEMINI_LANGUAGE,
                },
                tools=self.tools,
                system_instruction=self.prompt,
            )

            try:
                async with client.aio.live.connect(model=CONF_CHAT_MODEL, config=gemini_config) as session:
                    self.session = session
                    LOGGER.info("Gemini LiveAPI connection established.")

                    send_task = asyncio.create_task(self._send_to_gemini_task(webrtc_track))
                    recv_task = asyncio.create_task(self._receive_from_gemini_task())
                    playback_task = asyncio.create_task(self._playback_manager_task())

                    self.tasks = [send_task, recv_task, playback_task]
                    await asyncio.gather(*self.tasks)

            except TimeoutError as e:
                LOGGER.warning("Session timed out: %s, restarting...", e)
                await self.stop_session()

            except Exception as e:
                error_msg = str(e)
                if "BidiGenerateContent session not found" in error_msg:
                    LOGGER.warning("Gemini session invalid. Restarting...")
                    self.session_handle = None
                    await self.stop_session()
                else:
                    LOGGER.error("Fatal Gemini error: %s", e)
                    raise

    async def stop_session(self, **kwargs):
        # The sequence of active is important to prevent race condition
        if "active" in kwargs:
            self._active = kwargs["active"]

        if self.tasks:
            for task in self.tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks = []

        while not self.audio_playback_queue.empty():
            self.audio_playback_queue.get_nowait()

        if self.session:
            await self.session.close()
            self.session = None

        LOGGER.warning("Gemini session cleaning up. Full exit: %s", self._active == False)

    async def _playback_manager_task(self):
        """
        A dedicated, permanent task that pulls raw audio buffers from a queue
        and then calls the "slow" chunking function. This decouples playback
        from the main receive loop.
        """
        LOGGER.debug("Playback manager started.")
        try:
            while True:
                raw_buffer = await self.raw_audio_to_play_queue.get()
                await self._play_audio(raw_buffer)
                self.raw_audio_to_play_queue.task_done()
        except asyncio.CancelledError:
            LOGGER.debug("Playback manager cancelled.")

    async def _play_audio(self, full_audio_buffer: bytes):
        """
        Put fix-sized audio chunks to the audio playback queue which is then
        exposed to webrtc to consume. Without sleep, the webrtc audio parser
        will not process correctly.
        """
        for i in range(0, len(full_audio_buffer), CHUNK_SIZE_BYTES):
            chunk = full_audio_buffer[i:i + CHUNK_SIZE_BYTES]
            if not chunk:
                continue
            await self.audio_playback_queue.put(chunk)
            await asyncio.sleep(CHUNK_DURATION_MS / 1000)

    async def _receive_from_gemini_task(self):
        try:
            while True:
                turn = self.session.receive()
                async for response in turn:
                    if data := response.data:
                        LOGGER.debug(f"[Audio Bytes] [{self.remote_user_id}] {len(data)}")
                        if self.device.activity != "playing":
                            self.device.set_activity("playing")
                        await self.raw_audio_to_play_queue.put(bytes(data))
                    elif text := response.text:
                        LOGGER.debug(f"Gemini: {text}")
                    elif go_away := response.go_away:
                        raise TimeoutError(f"Gemini session timeout: {go_away.time_left}")

                    if response.session_resumption_update:
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            self.session_handle = update.new_handle

                    # The model might generate and execute Python code to use Search
                    if response.server_content:
                        if model_turn := response.server_content.model_turn:
                            for part in model_turn.parts:
                                if part.executable_code:
                                    LOGGER.debug("Code: %s", part.executable_code.code)
                                elif part.code_execution_result:
                                    LOGGER.debug("Code: %s", part.code_execution_result.output)

                        if response.server_content.interrupted is self.interrupt_enabled:
                            LOGGER.debug("VAD Interrupting.")
                            if not self.device.activity == "listening":
                                self.device.set_activity("listening")
                            while not self.raw_audio_to_play_queue.empty():
                                self.raw_audio_to_play_queue.get_nowait()
                            while not self.audio_playback_queue.empty():
                                self.audio_playback_queue.get_nowait()

                    elif response.tool_call:
                        function_responses = []
                        for fc in response.tool_call.function_calls:
                            tool_input = llm.ToolInput(
                                tool_name=fc.name,
                                tool_args = json.loads(json.dumps(fc.args, default=lambda o: getattr(o, "__dict__", str(o))))
                            )
                            LOGGER.info("FUNCTION CALL: %s", fc)
                            if fc.name in {tool for tool in self.hass_function_declarations_names}:
                                result = await self.llm_api.async_call_tool(tool_input)
                            elif fc.name == "good_bye":
                                self.is_wake.clear()
                                result = True
                                self.last_wake_time = asyncio.get_event_loop().time() # Reset last wake time
                                self.device.set_is_wake(False)
                            else:
                                result = {"error": f"Unknown function: {fc.name}"}

                            LOGGER.info("Function Response: %s", result)
                            function_response = types.FunctionResponse(
                                id=fc.id,
                                name=fc.name,
                                response={ "result": result}
                            )
                            function_responses.append(function_response)

                        await self.session.send_tool_response(function_responses=function_responses)

                    if response.server_content and response.server_content.turn_complete:
                        if not self.device.activity == "listening":
                            self.device.set_activity("listening")
                        break

        except asyncio.CancelledError:
            LOGGER.debug("Receive_from_gemini_task cancelled.")
        except TimeoutError as e:
            LOGGER.warning(f"Gemini Session Timeout: {e}")
            raise
        except Exception as e:
            LOGGER.error(f"Error in receive_from_gemini_task: {e}")
            raise


    async def _send_to_gemini_task(self, track):
        resampler = AudioResampler(format="s16", layout="mono", rate=GEMINI_SAMPLE_RATE)

        try:
            while True:
                frame = await track.recv()
                resampled_frames = resampler.resample(frame)

                for r_frame in resampled_frames:
                    audio_np = r_frame.to_ndarray().astype(np.int16).flatten()

                    if self.device.wake_word_enabled and self.wake_ws_client:
                        if not self.is_wake.is_set():
                            if not self.device.activity == "playing":
                                self.device.set_activity("idle")

                            # Accumulate audio until we have at least WAKE_BUFFER samples
                            self.wake_buffer = np.concatenate((self.wake_buffer, audio_np))

                            # Process chunks from the wake buffer
                            while len(self.wake_buffer) >= WAKE_BUFFER:
                                chunk = self.wake_buffer[:WAKE_BUFFER]
                                self.wake_buffer = self.wake_buffer[WAKE_BUFFER:]

                                scores = {}  # dict: model_name -> score (float)

                                # Try the remote WS client, if present
                                if self.wake_ws_client:
                                    try:
                                        resp = await self.wake_ws_client.predict_bytes(chunk.tobytes(), timeout=1.0)
                                        if isinstance(resp, dict):
                                            # accept either {"scores": {...}} or {"ok_nabu": 0.8} variants
                                            if "scores" in resp and isinstance(resp["scores"], dict):
                                                scores = resp["scores"]
                                            else:
                                                scores = resp
                                    except Exception as e:
                                        try:
                                            if self.wake_ws_client and getattr(self.wake_ws_client, "ws", None):
                                                await self.wake_ws_client.ws.close()
                                        except Exception:
                                            pass
                                        # remove reference so fallback logic runs
                                        self.wake_ws_client = None


                                # Evaluate detection using the 'scores' dict (not prediction_buffer directly)
                                detected = False
                                for mdl, sc in scores.items():
                                    try:
                                        if float(sc) > WAKE_THRESHOLD:
                                            LOGGER.info(f"[Wakeword '{mdl}'] detected with score {float(sc):.3f}")

                                            self.wake_buffer = np.array([], dtype=np.int16)
                                            current_time = asyncio.get_event_loop().time()
                                            if current_time - self.last_wake_time > DEBOUNCE_TIME:
                                                self.is_wake.set()
                                                self.last_wake_time = current_time
                                                self.device.set_is_wake(True)
                                            else:
                                                LOGGER.warning(f"[Wakeword '{mdl}'] debounced: < {DEBOUNCE_TIME}s")
                                            detected = True
                                            break
                                    except Exception as e:
                                        LOGGER.exception("Error handling wake score for %s: %s", mdl, e)

                                if detected:
                                    break  # break out of the while len(wake_buffer) loop


                        else:
                            # Send raw audio to Gemini once wake word detected
                            audio_bytes = audio_np.tobytes()
                            await self.session.send(
                                input={"data": audio_bytes, "mime_type": "audio/pcm"}
                            )
                    else:
                        # Display wake state
                        self.is_wake.set()
                        self.device.set_is_wake(True)

                        # Send raw audio to Gemini directly if wake word is disabled
                        audio_bytes = audio_np.tobytes()
                        await self.session.send(
                            input={"data": audio_bytes, "mime_type": "audio/pcm"}
                        )

        except MediaStreamError:
            LOGGER.debug("User audio track ended.")
        except asyncio.CancelledError:
            LOGGER.debug("Send_to_gemini_task cancelled.")
        except Exception as e:
            LOGGER.error(f"Error in send_to_gemini_task: {e}")
            raise
