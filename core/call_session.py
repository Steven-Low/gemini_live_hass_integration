import logging
from ..core.webrtc import WebRTCManager

LOGGER = logging.getLogger(__name__)

class CallSession:
    """
    Represents a single, self-contained call session.
    It manages its own WebRTC and LLM instances.
    """
    def __init__(self, hass, remote_user_id, signaling_client, llm_client, llm_track, on_cleanup_callback):
        LOGGER.debug(f"{remote_user_id}: Creating new call session.")
        self.hass = hass
        self.remote_user_id = remote_user_id
        self.signaling_client = signaling_client
        self.on_cleanup_callback = on_cleanup_callback

        # Each session gets its own, isolated managers.
        self.llm_client = llm_client(self.hass, self.remote_user_id)
        self.webrtc_manager = WebRTCManager(self.llm_client.audio_playback_queue, llm_track)

        self.cleaned_up = False
        self._wire_components()

    def _wire_components(self):
        """Wires the internal components for this specific session."""
        # WebRTC -> Gemini
        self.webrtc_manager.on_remote_track_callback = self.llm_client.start_session
        self.webrtc_manager.on_remote_video_track_callback = self.llm_client.start_video_processing

        # WebRTC -> Signaling (via this session)
        self.webrtc_manager.on_offer_created_callback = self._handle_offer_created
        self.webrtc_manager.on_answer_created_callback = self._handle_answer_created
        self.webrtc_manager.on_ice_candidate_callback = self._handle_ice_candidate

        # WebRTC -> Cleanup
        self.webrtc_manager.on_connection_closed_callback = self.cleanup

    # --- Methods to forward WebRTC events to the Signaling Client ---
    async def _handle_offer_created(self, sdp):
        await self.signaling_client.send_offer(self.remote_user_id, sdp)

    async def _handle_answer_created(self, sdp):
        await self.signaling_client.send_answer(self.remote_user_id, sdp)

    async def _handle_ice_candidate(self, candidate):
        await self.signaling_client.send_ice_candidate(self.remote_user_id, candidate)

    async def initiate_call(self):
        LOGGER.debug(f"{self.remote_user_id}: Initiating outbound call...")
        await self.webrtc_manager.create_offer()

    async def cleanup(self):
        """Shuts down all resources for this session."""
        if getattr(self, "cleaned_up", False):
            LOGGER.debug(f"{self.remote_user_id}: Cleanup already performed. Skipping.")
            return

        self.cleaned_up = True
        LOGGER.debug(f"{self.remote_user_id}: Cleaning up...")
        # await self.signaling_client.send_hangup(self.remote_user_id)
        await self.llm_client.stop_session()
        await self.webrtc_manager.close()
        # Notify the main application that this session is now over.
        if self.on_cleanup_callback:
            await self.on_cleanup_callback(self.remote_user_id)
