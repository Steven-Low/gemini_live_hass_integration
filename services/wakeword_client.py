import asyncio
import websockets
import logging
import json
from homeassistant.config_entries import ConfigEntry
from ..config.const import (
    CONF_WAKE_URL
)

LOGGER = logging.getLogger(__name__)

class WakeWordWSClient:
    def __init__(self, config_entry: ConfigEntry, reconnect_interval=3.0, max_retries=5):
        self.config_entry = config_entry
        self.ws = None
        self.reconnect_interval = reconnect_interval
        self.max_retries = max_retries
        self._lock = asyncio.Lock()
        self._connected_event = asyncio.Event()
        self._stopping = False

    def get_config(self, key):
        return self.config_entry.options.get(
            key, self.config_entry.data.get(key)
        )

    async def start(self):
        asyncio.create_task(self._ensure_connection_loop())

    async def _ensure_connection_loop(self):
        retry_count = 0

        while not self._stopping:
            if self.ws is None:
                try:
                    LOGGER.info("Connecting to WS: %s", self.get_config(CONF_WAKE_URL))
                    self.ws = await websockets.connect(self.get_config(CONF_WAKE_URL), max_size=None)
                    LOGGER.info("Connected!")
                    retry_count = 0
                    self._connected_event.set()
                except Exception as e:
                    LOGGER.warning("Connect failed: %s", e)
                    retry_count += 1
                    self._connected_event.clear()
                    self.ws = None

                    if retry_count >= self.max_retries:
                        LOGGER.error("Max retries reached, giving up.")
                        return  # EXIT TASK

                    await asyncio.sleep(self.reconnect_interval)
            else:
                await asyncio.sleep(0.1)

    async def _reconnect(self):
        """force reconnection when send/recv fails"""
        LOGGER.warning("WS seems broken, forcing reconnect...")
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
        self.ws = None
        self._connected_event.clear()
        await self.start()

    async def predict_bytes(self, audio_bytes: bytes, timeout: int = 3) -> dict:
        await self._connected_event.wait()

        async with self._lock:
            try:
                await self.ws.send(audio_bytes)
                msg = await asyncio.wait_for(self.ws.recv(), timeout)
                return json.loads(msg)
            except Exception as e:
                await self._reconnect()
                raise ConnectionError(f"WS send/recv failed: {e}")

    async def stop(self):
        self._stopping = True
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
        self.ws = None
        self._connected_event.clear()
