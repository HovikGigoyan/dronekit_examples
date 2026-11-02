"""
Drone control panel (Tkinter).

  [Connection string] -> [Connect] -> [Mode buttons / Arm / Takeoff] -> [Close Connection]

Run inside the container (needs a display):  python3 control_panel.py
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from dronekit import VehicleMode, connect

DEFAULT_CONNECTION = "127.0.0.1:14550"
MODES = ["GUIDED", "STABILIZE", "LOITER", "LAND", "RTL"]
DEFAULT_TAKEOFF_ALT = 10      # meters
STATUS_REFRESH_MS = 500
QUEUE_POLL_MS = 100
RC_MIN = 1000
RC_MID = 1500
RC_MAX = 2000
RC_SEND_MS = 100
KEY_RELEASE_DEBOUNCE_MS = 40
RC_CHANNEL = {"Roll": "1", "Pitch": "2", "Throttle": "3", "Yaw": "4"}
KEY_STEP = {"Roll": 150, "Pitch": 150, "Throttle": 200, "Yaw": 150}
KEYBOARD_MODES = ("LOITER",)



class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.vehicle = None
        self.results = queue.Queue()
        root.title("Drone Control Panel")
        root.resizable(False, False)

        # ---- BLOCK 1: Connection row ----
        top = ttk.Frame(root, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Connection string:").grid(row=0, column=0, padx=(0, 5))
        self.conn_var = tk.StringVar(value=DEFAULT_CONNECTION)
        self.conn_entry = ttk.Entry(top, textvariable=self.conn_var, width=28)
        self.conn_entry.grid(row=0, column=1, padx=5)
        self.connect_btn = ttk.Button(top, text="Connect", command=self.on_connect)
        self.connect_btn.grid(row=0, column=2, padx=5)

        # ---- BLOCK 2: Flight mode buttons ----
        modes = ttk.LabelFrame(root, text="Flight mode", padding=10)
        modes.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.mode_buttons = []
        for i, mode in enumerate(MODES):
            btn = ttk.Button(modes, text=mode.capitalize(),
                             command=lambda m=mode: self.set_mode(m))
            btn.grid(row=0, column=i, padx=3)
            self.mode_buttons.append(btn)

        # ---- BLOCK 3: Arm and close buttons ----
        actions = ttk.Frame(root, padding=10)
        actions.grid(row=2, column=0, sticky="ew")
        self.arm_btn = ttk.Button(actions, text="Arm", command=self.on_arm)
        self.arm_btn.grid(row=0, column=0, padx=5)
        self.close_btn = ttk.Button(actions, text="Close Connection", command=self.on_close)
        self.close_btn.grid(row=0, column=1, padx=5)

        # takeoff: altitude input + button
        ttk.Label(actions, text="Altitude (m):").grid(row=0, column=2, padx=(15, 3))
        self.alt_var = tk.StringVar(value=str(DEFAULT_TAKEOFF_ALT))
        self.alt_entry = ttk.Entry(actions, textvariable=self.alt_var, width=6)
        self.alt_entry.grid(row=0, column=3)
        self.takeoff_btn = ttk.Button(actions, text="Takeoff", command=self.on_takeoff)
        self.takeoff_btn.grid(row=0, column=4, padx=5)

        # ---- BLOCK 4: Current state ----
        state_frame = ttk.LabelFrame(root, text="Current State", padding=10)
        state_frame.grid(row=3, column=0, padx=10, pady=5, sticky="ew")
        self.connection_status = tk.StringVar(value="Disconnected")
        self.mode_status = tk.StringVar(value="N/A")
        self.arm_status = tk.StringVar(value="N/A")
        self.altitude_status = tk.StringVar(value="N/A")
        state_fields = [
            ("Connection:", self.connection_status),
            ("Mode:", self.mode_status),
            ("Armed:", self.arm_status),
            ("Altitude:", self.altitude_status)
        ]
 
        for i, (name, variable) in enumerate(state_fields):
            ttk.Label(state_frame, text=name).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(state_frame, textvariable=variable).grid(row=i, column=1, sticky="w", padx=15, pady=2)
        
        # ---- BLOCK 5: RC channels ----   
        rc_frame = ttk.LabelFrame(root, text="RC Channels", padding=10)
        rc_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.rc_values = {name: RC_MID for name in RC_CHANNEL}
        self.rc_bars = {}
        self.rc_labels = {}
 
        for i, (name, value) in enumerate(self.rc_values.items()):
            ttk.Label(rc_frame, text=name, width=10).grid(row=i, column=0, padx=5, pady=5)
 
            bar = ttk.Progressbar(
                rc_frame,
                orient="horizontal",
                length=260,
                mode="determinate",
                maximum=RC_MAX - RC_MIN
            )
            bar["value"] = value - RC_MIN
            bar.grid(row=i, column=1, padx=5)
 
            label = ttk.Label(rc_frame, text=str(value), width=6)
            label.grid(row=i, column=2, padx=5)
            self.rc_bars[name] = bar
            self.rc_labels[name] = label

        # ---- BLOCK 6: Keyboard controls ----
        keyboard_frame = ttk.LabelFrame(root, text="Keyboard Controls", padding=10)
        keyboard_frame.grid(row=5, column=0, padx=10, pady=5, sticky="ew")
        ttk.Label(keyboard_frame, text="W / S : Climb / Descend       A / D : Yaw").pack()
        ttk.Label(keyboard_frame, text="Left / Right : Roll       Up / Down : Pitch (fwd / back)").pack(pady=5)
        self.rc_state_var = tk.StringVar(value="RC override: off")
        ttk.Label(keyboard_frame, textvariable=self.rc_state_var).pack(pady=(5, 0))
 
        self.keymap = {
            "w": ("Throttle", +1),
            "s": ("Throttle", -1),
            "a": ("Yaw", -1),
            "d": ("Yaw", +1),
            "Left": ("Roll", -1),
            "Right": ("Roll", +1),
            "Up": ("Pitch", -1),
            "Down": ("Pitch", +1)
        }
 
        self.pressed_keys = set()
        self.release_jobs = {}
        self.rc_active = False
        root.bind("<KeyPress>", self.on_key_press)
        root.bind("<KeyRelease>", self.on_key_release)
        root.bind("<FocusOut>", self.on_focus_out)
            
        # ---- BLOCK 7: Status bar ----
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(root, textvariable=self.status_var, relief="sunken", anchor="w", padding=4).grid(row=6, column=0, sticky="ew")
        self.set_connected_ui(False)
        root.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.refresh_status()
        self.poll_results()
        self.rc_tick()

    # ---------- UI helpers ----------
    def set_connected_ui(self, connected):
        state = "normal" if connected else "disabled"
        for btn in self.mode_buttons + [self.arm_btn, self.close_btn,
                                         self.alt_entry, self.takeoff_btn]:
            btn.config(state=state)
        self.connect_btn.config(state="disabled" if connected else "normal")
        self.conn_entry.config(state="disabled" if connected else "normal")
 
    def refresh_status(self):
        """Show mode and armed state of the vehicle, twice per second."""
        if self.vehicle is not None:
            try:
                self.connection_status.set("Connected")
                self.mode_status.set(self.vehicle.mode.name)
                armed = self.vehicle.armed
                self.arm_status.set("ARMED" if armed else "Disarmed")
                alt = self.vehicle.location.global_relative_frame.alt
                self.altitude_status.set("%.1f m" % (alt or 0))
            except Exception as error:
                self.status_var.set("Telemetry error: %s" % error)
 
        self.root.after(STATUS_REFRESH_MS, self.refresh_status)
        
    # ---------- RC visualization ----------
    def update_rc(self, name, value):
        value = max(RC_MIN, min(RC_MAX, value))
        self.rc_values[name] = value
        self.rc_bars[name]["value"] = value - RC_MIN
        self.rc_labels[name]["text"] = str(value)
 
    def apply_keys(self):
        values = {name: RC_MID for name in RC_CHANNEL}
        for key in self.pressed_keys:
            name, direction = self.keymap[key]
            values[name] += direction * KEY_STEP[name]
        for name, value in values.items():
            self.update_rc(name, value)
 
    def reset_rc(self):
        for job in self.release_jobs.values():
            self.root.after_cancel(job)
        self.release_jobs.clear()
        self.pressed_keys.clear()
        self.apply_keys()
        
        # ---------- Keyboard ----------
    def key_name(self, event):
        key = event.keysym
        return key.lower() if len(key) == 1 else key
 
    def typing_in_entry(self):
        try:
            return isinstance(self.root.focus_get(), tk.Entry)
        except KeyError:
            return False
 
    def on_key_press(self, event):
        key = self.key_name(event)
        if key not in self.keymap or self.typing_in_entry():
            return
 
        job = self.release_jobs.pop(key, None)
        if job is not None:
            self.root.after_cancel(job)
 
        if key in self.pressed_keys:
            return
 
        self.pressed_keys.add(key)
        self.apply_keys()
 
    def on_key_release(self, event):
        key = self.key_name(event)
        if key not in self.pressed_keys or key in self.release_jobs:
            return

        self.release_jobs[key] = self.root.after(
            KEY_RELEASE_DEBOUNCE_MS, lambda k=key: self.finish_release(k))
 
    def finish_release(self, key):
        self.release_jobs.pop(key, None)
        self.pressed_keys.discard(key)
        self.apply_keys()
 
    def on_focus_out(self, event):
        self.reset_rc()
 
    # ---------- RC override to the vehicle ----------
    def rc_block_reason(self):
        if self.vehicle is None:
            return "not connected"
        mode = self.vehicle.mode.name
        if mode not in KEYBOARD_MODES:
            return "switch to %s" % " / ".join(KEYBOARD_MODES)
        return None
 
    def sync_rc_override(self):
        if self.vehicle is None:
            self.rc_active = False
            return
        try:
            reason = self.rc_block_reason()
            if reason is None:
                overrides = self.vehicle.channels.overrides
                for name, channel in RC_CHANNEL.items():
                    overrides[channel] = self.rc_values[name]
                self.rc_active = True
                self.rc_state_var.set("RC override: ACTIVE (%s)" % self.vehicle.mode.name)
            else:
                if self.rc_active:
                    self.release_rc(self.vehicle)
                self.rc_state_var.set("RC override: off (%s)" % reason)
        except Exception as error:
            self.status_var.set("RC override error: %s" % error)
 
    def release_rc(self, vehicle):
        self.rc_active = False
        try:
            vehicle.channels.overrides = {}
        except Exception as error:
            self.status_var.set("RC release error: %s" % error)
 
    def rc_tick(self):
        self.sync_rc_override()
        self.root.after(RC_SEND_MS, self.rc_tick)

    # ---------- Connect ----------
    def on_connect(self):
        self.connect_btn.config(state="disabled")
        self.status_var.set("Connecting...")
        self.connection_status.set("Connecting...")
        # connect() blocks for several seconds, so run it off the UI thread
        threading.Thread(target=self.connect_worker,
                         args=(self.conn_var.get().strip(),), daemon=True).start()
 
    def connect_worker(self, conn_string):
        try:
            self.results.put(("connected", connect(conn_string, wait_ready=True, timeout=30)))
        except Exception as e:
            self.results.put(("failed", e))
 
    def poll_results(self):
        try:
            while True:
                kind, value = self.results.get_nowait()
                if kind == "connected":
                    self.connect_done(value)
                else:
                    self.connect_failed(value)
        except queue.Empty:
            pass
        self.root.after(QUEUE_POLL_MS, self.poll_results)
 
    def connect_done(self, vehicle):
        self.vehicle = vehicle
        self.set_connected_ui(True)
        self.connection_status.set("Connected")
        self.status_var.set("Connected")
 
    def connect_failed(self, error):
        self.status_var.set("Connection failed: %s" % error)
        self.connection_status.set("Disconnected")
        self.connect_btn.config(state="normal")

    # ---------- Commands ----------
    def set_mode(self, mode):
        self.vehicle.mode = VehicleMode(mode)
 
    def on_arm(self):
        if not self.vehicle.is_armable:
            self.status_var.set("Cannot arm yet: vehicle not armable (wait for GPS/EKF)")
            return
        self.vehicle.mode = VehicleMode("GUIDED")   # arming from code needs GUIDED
        self.vehicle.armed = True
 
    def on_takeoff(self):
        try:
            altitude = float(self.alt_var.get())
            if altitude <= 0:
                raise ValueError
        except ValueError:
            self.status_var.set("Enter a takeoff altitude in meters (a number above 0)")
            return
        if not self.vehicle.armed:
            self.status_var.set("Arm the vehicle first")
            return
        self.vehicle.mode = VehicleMode("GUIDED")   # simple_takeoff needs GUIDED
        self.vehicle.simple_takeoff(altitude)
 
    def on_close(self):
        vehicle, self.vehicle = self.vehicle, None
        self.release_rc(vehicle)
        self.rc_state_var.set("RC override: off")
        self.set_connected_ui(False)
        self.status_var.set("Not connected")
        self.connection_status.set("Disconnected")
        self.mode_status.set("N/A")
        self.arm_status.set("N/A")
        self.altitude_status.set("N/A")
        self.reset_rc()
 
        threading.Thread(target=vehicle.close, daemon=True).start()
 
    def on_exit(self):
        if self.vehicle is not None:
            self.release_rc(self.vehicle)
            self.vehicle.close()
        self.root.destroy()
 
 
if __name__ == "__main__":
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()