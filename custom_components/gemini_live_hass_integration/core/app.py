import logging
import asyncio

from ..core.signaling import SignalingClient
from ..core.cli import CLIHandler
from ..config.const import MAX_SESSIONS, DOMAIN
from ..config.factories import create_call_session
from ..models.devices import GeminiLiveDevice

from collections.abc import Callable
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_EXTERNAL_URL
from homeassistant.helpers import config_validation as cv, device_registry as dr

LOGGER = logging.getLogger(__name__)

class GeminiApp:
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        self.hass = hass
        self.config_entry = config_entry
        self.main_caller_id = "666666"
        self.active_sessions = {}
        self.signaling_client = None
        self.llm_name = "gemini"
        # self.cli = CLIHandler(self)
        self.entity_adders: list[Callable[[GeminiLiveDevice], None]] = []  # support multiple platforms
        self._wire_signaling()

    def register_entity_adder(self, cb: Callable[[GeminiLiveDevice], None]) -> None:
        """Register a platform callback for adding entities."""
        self.entity_adders.append(cb)

    def _wire_signaling(self):
        """Wires up the signaling client to the application's handlers."""
        LOGGER.info("Initalizing Signalling Server: %s", self.config_entry.options.get(CONF_EXTERNAL_URL))
        self.signaling_client = SignalingClient(self.config_entry.options.get(CONF_EXTERNAL_URL))
        self.signaling_client.on_connect_callback = lambda: LOGGER.info(f"Connected to signaling. My main ID is: {self.main_caller_id}")
        self.signaling_client.on_new_call_callback = self.handle_incoming_call
        self.signaling_client.on_call_answered_callback = self.handle_call_answered
        self.signaling_client.on_ice_candidate_callback = self.handle_ice_candidate
        self.signaling_client.on_call_ended_callback = self.handle_call_ended

    # --- Signaling Handler Methods ---
    async def handle_incoming_call(self, data):
        caller_id = data.get('callerId')
        rtc_message = data.get('rtcMessage')

        LOGGER.info(f"Incoming call from {caller_id} to main ID.")

        if len(self.active_sessions) >= MAX_SESSIONS:
            LOGGER.warning(f"At max capacity ({MAX_SESSIONS} calls). Rejecting call from {caller_id}.")
            return

        session = await self._create_and_register_session(caller_id)
        await session.webrtc_manager.handle_remote_offer(rtc_message)

    async def handle_call_answered(self, data):
        callee_id = data.get('callee')
        session = self.active_sessions.get(callee_id)
        if session:
            await session.webrtc_manager.handle_remote_answer(data.get('rtcMessage'))

    async def handle_call_ended(self, data):
        caller_id = data.get("senderId")
        LOGGER.warning(f"Caller {caller_id} hung up before call connected.")
        session = self.active_sessions.get(caller_id)
        if session:
            await session.cleanup()

    async def handle_ice_candidate(self, data):
        # Find the correct session and delegate the ICE candidate
        sender_id = data.get('sender')
        session = self.active_sessions.get(sender_id)
        if session:
            await session.webrtc_manager.add_ice_candidate(data)
    # ----------------------------------

    async def _create_and_register_session(self, remote_user_id: str):
        """Creates a call session, stores it, and registers a device in Home Assistant."""
        LOGGER.info(f"Creating session and registering device for user {remote_user_id}.")

        gemini_device = await self._create_new_device(remote_user_id=remote_user_id)

        session = create_call_session(
            hass=self.hass,
            config_entry=self.config_entry,
            device=gemini_device,
            remote_user_id=remote_user_id,
            signaling_client=self.signaling_client,
            on_cleanup_callback=self.remove_session,
            llm_name=self.llm_name
        )

        self.active_sessions[remote_user_id] = session
        return session

    async def _create_new_device(self, remote_user_id) -> GeminiLiveDevice:
        """Called when a new Gemini device/session is discovered."""
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, remote_user_id)},
            name=f"Gemini Session - {remote_user_id}",
            model="Gemini Live Session",
            manufacturer="Google",
        )
        gemini_device = GeminiLiveDevice(
            processor_id=remote_user_id,
            device_id=device.id
        )
        LOGGER.info(f"Device registered for session {remote_user_id}.")

        for adder in self.entity_adders:
            await adder(gemini_device)
        LOGGER.info(f"Entities registered for device {device.id}.")

        return gemini_device

    async def remove_session(self, session_id):
        """Callback function to remove a session when it has finished cleaning up."""
        if session_id in self.active_sessions:
            session = self.active_sessions.pop(session_id, None)
            # del self.active_sessions[session_id]
            if session:
                LOGGER.info(f"Removing session {session_id} from active list.")
                # await session.cleanup()

            dev_reg = dr.async_get(self.hass)
            device = dev_reg.async_get_device(identifiers={(DOMAIN, session_id)})
            if device:
                dev_reg.async_remove_device(device.id)
                LOGGER.info(f"Removing device {device.id} from device registry.")


        LOGGER.info(f"Current active sessions: {len(self.active_sessions)}")

    async def start_call(self, target_id):
        """Initiates an outbound call to a target user."""
        LOGGER.info(f"Attempting to start call to {target_id}.")

        if target_id in self.active_sessions:
            LOGGER.warning(f"Already in an active session with {target_id}. Cannot start a new call.")
            return

        if len(self.active_sessions) >= MAX_SESSIONS:
            LOGGER.warning(f"At max capacity ({MAX_SESSIONS} calls). Cannot start a new call.")
            return

        LOGGER.info(f"Creating new session for outbound call to {target_id}.")

        session = await self._create_and_register_session(target_id)

        try:
            await session.initiate_call()
            LOGGER.info(f"Offer sent to {target_id}. Waiting for them to answer.")
        except Exception as e:
            LOGGER.error(f"Failed to initiate call to {target_id}. Error: {e}")
            await session.cleanup()

    async def hang_up(self, session_id_to_hang_up):
        """Hangs up a specific call by its ID."""
        LOGGER.info(f"Attempting to hang up session {session_id_to_hang_up}.")
        session = self.active_sessions.get(session_id_to_hang_up)
        if session:
            # This will trigger the session's internal cleanup, which will then call remove_session
            await session.cleanup()
        else:
            LOGGER.warning(f"No active session found with ID {session_id_to_hang_up}.")

    async def shutdown(self):
        LOGGER.warning("Shutting down application...")
        # Create a copy of the sessions to iterate over, as cleanup will modify the dict
        all_sessions = list(self.active_sessions.values())
        for session in all_sessions:
            await session.cleanup()
        await self.signaling_client.disconnect()

    async def run(self):
        try:
            await self._setup_socket_connection(max_retries=3)
            while True:
                await asyncio.sleep(3600)
            # await self.cli.loop() # Assuming the CLI now calls hang_up with a specific ID
        except ConnectionRefusedError as e:
            LOGGER.error("Please check your signalling socket server url/port configuration.")
        except Exception as e:
            LOGGER.error(f"An error occurred in the application: {e}")
        finally:
            await self.shutdown()

    async def _setup_socket_connection(self, max_retries: int = 3):
        """Try to connect to signaling server with retry + backoff."""
        attempt = 0
        delay = 2

        while True:
            try:
                await self.signaling_client.connect(self.main_caller_id)
                LOGGER.info("Successfully connected to signaling server.")
                return
            except Exception as e:
                attempt += 1
                if max_retries and attempt > max_retries:
                    LOGGER.error(f"Failed to connect after {attempt-1} retries. Error: {e}")
                    raise ConnectionRefusedError

                LOGGER.warning(
                    f"Connection attempt {attempt} failed: {e}. "
                    f"Retrying in {delay:.1f} seconds..."
                )
                await asyncio.sleep(delay)
