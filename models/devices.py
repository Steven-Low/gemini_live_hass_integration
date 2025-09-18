from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.core import callback


@dataclass
class GeminiLiveDevice:
    """Minimal state manager for Gemini Manager."""

    processor_id: str
    device_id: str

    # Core states
    is_wake: bool = False                 # Actual wakeword/session state (triggered)
    activity: str = "idle"                # idle | listening | playing
    wake_word_enabled: bool = True             # Whether wake word detection is enabled

    # Optional listeners
    _wake_listener: Callable[[], None] | None = None
    _activity_listener: Callable[[], None] | None = None
    _wake_word_enabled_listener: Callable[[], None] | None = None

    # ----------------------
    # Update methods
    # ----------------------
    @callback
    def set_is_wake(self, wake: bool) -> None:
        if wake != self.is_wake:
            self.is_wake = wake
            if self._wake_listener:
                self._wake_listener()

    @callback
    def set_activity(self, state: str) -> None:
        if state != self.activity:
            self.activity = state
            if self._activity_listener:
                self._activity_listener()

    @callback
    def set_wake_word_enabled(self, enabled: bool) -> None:
        if enabled != self.wake_word_enabled:
            self.wake_word_enabled = enabled
            if self._wake_word_enabled_listener:
                self._wake_word_enabled_listener()

    # ----------------------
    # Listener registration
    # ----------------------
    @callback
    def set_wake_listener(self, cb: Callable[[], None]) -> None:
        self._wake_listener = cb

    @callback
    def set_activity_listener(self, cb: Callable[[], None]) -> None:
        self._activity_listener = cb

    @callback
    def set_wake_word_enabled_listener(self, cb: Callable[[], None]) -> None:
        self._wake_word_enabled_listener = cb
