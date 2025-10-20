import asyncio
import websockets
import logging
import json

LOGGER = logging.getLogger(__name__)

class WakeWordWSClient:
    def __init__(self, url: str = "ws://10.10.10.124:8001/ws", reconnect_interval: float = 1.0):
        self.url = url
        self.ws = None
        self.reconnect_interval = reconnect_interval
        self._lock = asyncio.Lock()
        self._connected_event = asyncio.Event()
        self._stopping = False

    async def start(self):
        asyncio.create_task(self._ensure_connection_loop())

    async def _ensure_connection_loop(self):
        while not self._stopping:
            if self.ws is None:
                try:
                    LOGGER.info("Connecting to WakeWord WS at %s", self.url)
                    self.ws = await websockets.connect(self.url, max_size=None)
                    self._connected_event.set()
                    LOGGER.info("Connected to WakeWord WS")
                except Exception as e:
                    LOGGER.warning("Failed to connect: %s. Retrying in %.1fs", e, self.reconnect_interval)
                    self._connected_event.clear()
                    self.ws = None
                    await asyncio.sleep(self.reconnect_interval)
            else:
                await asyncio.sleep(0.1)

    async def predict_bytes(self, audio_bytes: bytes, timeout: int) -> dict:
        if self.ws is None:
            raise ConnectionError("WakeWord WS not connected")

        async with self._lock:
            await self.ws.send(audio_bytes)
            msg = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            return json.loads(msg)

    async def stop(self):
        self._stopping = True
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        self._connected_event.clear()
