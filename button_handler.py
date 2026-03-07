"""
button_handler.py
-----------------
Handles the physical/keyboard help button with smart error detection.

LOGIC:
  - User must HOLD the button for 2 seconds → counts as INTENTIONAL (triggers recording)
  - Quick tap (under 2 seconds)             → counts as ACCIDENTAL (ignored)
  - After a trigger, there's a 5-second cooldown to prevent spam

This is run as a background listener. Press SPACE BAR to simulate the button,
or integrate with a physical GPIO button / USB button if needed.
"""

import time
import threading

# ── Tuning knobs ─────────────────────────────────────────────────────────────
HOLD_SECONDS  = 2.0   # How long to hold before it's "intentional"
COOLDOWN_SEC  = 5.0   # Ignore re-presses this many seconds after a trigger
# ─────────────────────────────────────────────────────────────────────────────


class ButtonHandler:
    """
    Tracks button press/release timing to distinguish accidental from intentional.

    Usage:
        handler = ButtonHandler(on_intentional_press=my_callback)
        handler.simulate_press()   # or wire to real button
    """

    def __init__(self, on_intentional_press=None):
        """
        Args:
            on_intentional_press: A function to call when an intentional press is detected.
                                  It will receive no arguments.
        """
        self.on_intentional_press = on_intentional_press
        self._press_start_time    = None
        self._last_trigger_time   = 0
        self._lock                = threading.Lock()
        self._hold_timer          = None

    def press_down(self):
        """Call this when the button is pressed DOWN."""
        with self._lock:
            now = time.time()

            # Check cooldown — ignore if too soon after last trigger
            time_since_last = now - self._last_trigger_time
            if time_since_last < COOLDOWN_SEC:
                remaining = COOLDOWN_SEC - time_since_last
                print(f"⏳ Cooldown active — please wait {remaining:.1f}s before pressing again.")
                return

            self._press_start_time = now
            print(f"🔘 Button held... (hold for {HOLD_SECONDS}s to confirm help request)")

            # Start a background timer — fires if user holds long enough
            self._hold_timer = threading.Timer(HOLD_SECONDS, self._on_hold_confirmed)
            self._hold_timer.start()

    def press_up(self):
        """Call this when the button is RELEASED."""
        with self._lock:
            if self._press_start_time is None:
                return  # No press was registered

            hold_duration = time.time() - self._press_start_time
            self._press_start_time = None

            # Cancel the hold timer if still running
            if self._hold_timer and self._hold_timer.is_alive():
                self._hold_timer.cancel()
                self._hold_timer = None
                # Released before hold threshold → accidental
                print(f"⚠️  Accidental press detected (held {hold_duration:.2f}s < {HOLD_SECONDS}s) — ignored.")
                return "accidental"

        return "intentional"  # Timer already fired, already handled

    def _on_hold_confirmed(self):
        """Fires after button has been held for HOLD_SECONDS — it's intentional!"""
        with self._lock:
            self._last_trigger_time = time.time()
            self._press_start_time  = None

        print(f"\n✅ Intentional help request confirmed! Starting recording...")

        if self.on_intentional_press:
            # Run callback in a new thread so button handler stays responsive
            thread = threading.Thread(target=self.on_intentional_press, daemon=True)
            thread.start()

    def simulate_press(self, hold_duration=2.5):
        """
        Simulates a button press for testing purposes.

        Args:
            hold_duration: Seconds to simulate holding the button.
                           Use < 2.0 to test accidental press detection.
        """
        print(f"\n🧪 Simulating button press (held for {hold_duration}s)...")
        self.press_down()
        time.sleep(hold_duration)
        self.press_up()


def start_keyboard_listener(on_intentional_press):
    """
    Starts listening for SPACE BAR as a simulated help button.
    Press and hold SPACE for 2 seconds = intentional help request.
    Quick tap = accidental (ignored).

    Args:
        on_intentional_press: Function to call when intentional press is detected.

    Returns:
        The ButtonHandler instance (call .stop() to quit).
    """
    try:
        from pynput import keyboard
    except ImportError:
        print("⚠️  pynput not installed. Run: pip install pynput")
        print("   Falling back to simulation mode.")
        return None

    handler = ButtonHandler(on_intentional_press=on_intentional_press)
    pressed_keys = set()

    def on_press(key):
        if key == keyboard.Key.space and key not in pressed_keys:
            pressed_keys.add(key)
            handler.press_down()

    def on_release(key):
        if key == keyboard.Key.space and key in pressed_keys:
            pressed_keys.discard(key)
            handler.press_up()
        if key == keyboard.Key.esc:
            print("\n👋 Stopped listening for button presses.")
            return False  # Stop listener

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    print("🎯 Button listener active!")
    print("   → Hold SPACE for 2 seconds = Help request")
    print("   → Quick tap              = Ignored (accidental)")
    print("   → Press ESC              = Quit")

    return handler, listener
