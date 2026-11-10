"""
Drone control panel (Tkinter).

  [Connection string] -> [Connect] -> [Mode buttons / Arm / Takeoff] -> [Close Connection]

Run inside the container (needs a display):  python3 control_panel.py
"""
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from dronekit import LocationGlobalRelative, VehicleMode, connect
from PIL import Image, ImageDraw, ImageTk
from tkintermapview import TkinterMapView

DEFAULT_CONNECTION = "127.0.0.1:14550"
MODES = ["GUIDED", "STABILIZE", "LOITER", "LAND", "RTL"]
DEFAULT_TAKEOFF_ALT = 10
STATUS_REFRESH_MS = 500
QUEUE_POLL_MS = 100
TELEMETRY_RATE_HZ = 10
RC_MIN = 1000
RC_MID = 1500
RC_MAX = 2000
RC_TICK_MS = 50
KEY_RELEASE_DEBOUNCE_MS = 40
RC_CHANNEL = {"Roll": "1", "Pitch": "2", "Throttle": "3", "Yaw": "4"}
MAX_OFFSET = {"Roll": 250, "Pitch": 250, "Throttle": 300, "Yaw": 200}
RAMP_UP_PER_S = 250 
RAMP_DOWN_PER_S = 400
NO_KEYBOARD_MODES = ("GUIDED",)
MAP_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MAP_MAX_ZOOM = 19
MAP_START = (-35.363261, 149.165230)
MAP_START_ZOOM = 17
MAP_FOLLOW_ZOOM = 18
MAP_REFRESH_MS = 500
TRAIL_STEP_M = 2.0
TRAIL_MAX_POINTS = 600
MIN_FLY_ALT_M = 1.0



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

        # ---- takeoff ---- 
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
        self.heading_status = tk.StringVar(value="N/A")
        self.rc_state_var = tk.StringVar(value="N/A")
        state_fields = [
            ("Connection:", self.connection_status),
            ("Mode:", self.mode_status),
            ("Armed:", self.arm_status),
            ("Altitude:", self.altitude_status),
            ("Heading:", self.heading_status),
            ("RC override:", self.rc_state_var)
        ]
 
        for i, (name, variable) in enumerate(state_fields):
            ttk.Label(state_frame, text=name).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(state_frame, textvariable=variable).grid(row=i, column=1, sticky="w", padx=15, pady=2)
        
        # ---- BLOCK 5: RC channels ----   
        rc_frame = ttk.LabelFrame(root, text="RC Channels", padding=10)
        rc_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.rc_values = {name: None for name in RC_CHANNEL}
        self.rc_cmd = {name: 0.0 for name in RC_CHANNEL}
        self.rc_bars = {}       
        self.rc_labels = {}
 
        for i, name in enumerate(RC_CHANNEL):
            ttk.Label(rc_frame, text=name, width=10).grid(row=i, column=0, padx=5, pady=5)
 
            bar = ttk.Progressbar(
                rc_frame,
                orient="horizontal",
                length=260,
                mode="determinate",
                maximum=RC_MAX - RC_MIN
            )
            bar["value"] = 0
            bar.grid(row=i, column=1, padx=5)
 
            label = ttk.Label(rc_frame, text='-', width=6)
            label.grid(row=i, column=2, padx=5)
            self.rc_bars[name] = bar
            self.rc_labels[name] = label

        # ---- BLOCK 6: Keyboard controls ----
        keyboard_frame = ttk.LabelFrame(root, text="Keyboard Controls", padding=10)
        keyboard_frame.grid(row=5, column=0, padx=10, pady=5, sticky="ew")
        ttk.Label(keyboard_frame, text="W / S : Climb / Descend       A / D : Yaw").pack()
        ttk.Label(keyboard_frame, text="Left / Right : Roll       Up / Down : Pitch (fwd / back)").pack(pady=5)
 
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
        self.last_tick = time.monotonic()
        root.bind("<KeyPress>", self.on_key_press)
        root.bind("<KeyRelease>", self.on_key_release)
        root.bind("<FocusOut>", self.on_focus_out)
        
        # ---- BLOCK 7: right column of Map ----
        map_frame = ttk.LabelFrame(root, text="Map", padding=5)
        map_frame.grid(row=0, column=1, rowspan=7, padx=(0, 10), pady=5, sticky="nsew")
        self.map_view = TkinterMapView(map_frame, width=640, height=520, corner_radius=0)
        self.map_view.pack(fill="both", expand=True)
        self.map_view.set_tile_server(MAP_TILE_URL, max_zoom=MAP_MAX_ZOOM)
        self.map_view.set_position(*MAP_START)
        self.map_view.set_zoom(MAP_START_ZOOM)
        self.map_view.add_left_click_map_command(self.on_map_click)
        self.map_view.add_right_click_menu_command("Fly here", self.on_map_fly_here, pass_coords=True)
        self.map_view.add_right_click_menu_command("Clear target", self.clear_target)
        map_bar = ttk.Frame(map_frame)
        map_bar.pack(fill="x", pady=(5, 0))
        self.fly_btn = ttk.Button(map_bar, text="Fly to target", command=self.fly_to_target)
        self.fly_btn.pack(side="left")
        ttk.Button(map_bar, text="Clear target", command=self.clear_target).pack(side="left", padx=5)
        ttk.Button(map_bar, text="Center on drone", command=self.center_on_drone).pack(side="left")
        self.target_var = tk.StringVar(value="Target: none")
        ttk.Label(map_bar, textvariable=self.target_var).pack(side="left", padx=10)
        self.drone_marker = None
        self.drone_icons = {}
        self.last_position = None
        self.map_centered = False
        self.trail = []
        self.trail_path = None
        self.target = None
        self.target_marker = None
            
    # ---- BLOCK 8: Status bar ----
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(root, textvariable=self.status_var, relief="sunken", anchor="w", padding=4).grid(row=6, column=0, sticky="ew")
        self.set_connected_ui(False)
        root.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.refresh_status()
        self.poll_results()
        self.rc_tick()
        self.map_tick()
        
    # ---- Helper functions -----
    @staticmethod
    def clamp_pwm(value):
        return max(RC_MIN, min(RC_MAX, value))

    @staticmethod
    def ramp(current, target, dt):
        if target == 0 or (current != 0 and (target > 0) != (current > 0)):
            goal, rate = 0.0, RAMP_DOWN_PER_S
        else:
            goal = float(target)
            rate = RAMP_UP_PER_S if abs(target) > abs(current) else RAMP_DOWN_PER_S
    
        step = rate * dt
        if abs(goal - current) <= step:
            return goal
        return current + step if goal > current else current - step

    @staticmethod
    def distance_m(lat1, lon1, lat2, lon2):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dlat = p2 - p1
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
        return 2 * 6371000.0 * math.asin(math.sqrt(a))

    @staticmethod
    def make_drone_icon(heading, size=44):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        c = size / 2
        ImageDraw.Draw(img).polygon(
            [(c, 3), (size - 9, size - 7), (c, size - 15), (9, size - 7)],
            fill=(255, 255, 255, 255), outline=(0, 120, 255, 255))
        return ImageTk.PhotoImage(img.rotate(-heading, resample=Image.BICUBIC))
        
    # ---------- UI helpers ----------
    def set_connected_ui(self, connected):
        state = "normal" if connected else "disabled"
        for btn in self.mode_buttons + [self.arm_btn, self.close_btn,
                                         self.alt_entry, self.takeoff_btn, self.fly_btn]:
            btn.config(state=state)
        self.connect_btn.config(state="disabled" if connected else "normal")
        self.conn_entry.config(state="disabled" if connected else "normal")
 
    def refresh_status(self):
        if self.vehicle is not None:
            try:
                self.connection_status.set("Connected")
                self.mode_status.set(self.vehicle.mode.name)
                armed = self.vehicle.armed
                self.arm_status.set("ARMED" if armed else "Disarmed")
                alt = self.vehicle.location.global_relative_frame.alt
                self.altitude_status.set("%.1f m" % (alt or 0))
                heading = self.vehicle.heading
                self.heading_status.set("%d\N{DEGREE SIGN}" % heading if heading is not None else "N/A")
            except Exception as error:
                self.status_var.set("Telemetry error: %s" % error)
 
        self.root.after(STATUS_REFRESH_MS, self.refresh_status)
        
    # ---------- RC visualization ----------
    def on_rc_message(self, vehicle, name, msg):
        if getattr(msg, "port", 0) != 0:
            return
        for rc_name, channel in RC_CHANNEL.items():
            value = getattr(msg, "chan%s_raw" % channel, 0)
            self.rc_values[rc_name] = value if 0 < value < 65535 else None
            
    def show_rc(self):
        for name in RC_CHANNEL:
            value = self.rc_values.get(name)
            if value is None:
                self.rc_bars[name]["value"] = 0
                self.rc_labels[name]["text"] = ""
            else:
                self.rc_bars[name]["value"] = self.clamp_pwm(value) - RC_MIN
                self.rc_labels[name]["text"] = str(value)
        
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
 
        self.pressed_keys.add(key)
 
    def on_key_release(self, event):
        key = self.key_name(event)
        if key not in self.pressed_keys or key in self.release_jobs:
            return

        self.release_jobs[key] = self.root.after(
            KEY_RELEASE_DEBOUNCE_MS, lambda k=key: self.finish_release(k))
 
    def finish_release(self, key):
        self.release_jobs.pop(key, None)
        self.pressed_keys.discard(key)
 
    def on_focus_out(self, event):
        self.clear_keys()

    def clear_keys(self):
        for job in self.release_jobs.values():
            self.root.after_cancel(job)
        self.release_jobs.clear()
        self.pressed_keys.clear()
        
    def target_offsets(self):
        direction = {name: 0 for name in RC_CHANNEL}
        for key in self.pressed_keys:
            name, sign = self.keymap[key]
            direction[name] += sign
        return {name: max(-1, min(1, d)) * MAX_OFFSET[name] for name, d in direction.items()}
 
    def zero_cmd(self):
        for name in self.rc_cmd:
            self.rc_cmd[name] = 0.0
 
    # ---------- RC override to the vehicle ----------
    def rc_block_reason(self):
        if self.vehicle is None:
            return "not connected"
        return None
 
    def sync_rc_override(self, reason, armed, keys_on):
        if self.vehicle is None:
            self.rc_active = False
            return
        try:
            if reason is None:
                overrides = self.vehicle.channels.overrides
                for name, channel in RC_CHANNEL.items():
                    overrides[channel] = self.clamp_pwm(RC_MID + int(round(self.rc_cmd[name])))
                if not armed:
                    overrides[RC_CHANNEL["Throttle"]] = RC_MIN
                self.rc_active = True
                self.rc_state_var.set("ACTIVE" if keys_on else "off")
            else:
                if self.rc_active:
                    self.release_rc(self.vehicle)
                self.rc_state_var.set("N/A")
        except Exception as error:
            self.status_var.set("RC override error: %s" % error)
 
    def release_rc(self, vehicle):
        self.rc_active = False
        try:
            vehicle.channels.overrides = {}
        except Exception as error:
            self.status_var.set("RC release error: %s" % error)
            
    def control_step(self, dt):
        reason = self.rc_block_reason()
        armed = reason is None and bool(self.vehicle.armed)
        keys_on = reason is None and self.vehicle.mode.name not in NO_KEYBOARD_MODES
        if armed and keys_on:
            targets = self.target_offsets()
            for name in RC_CHANNEL:
                self.rc_cmd[name] = self.ramp(self.rc_cmd[name], targets[name], dt)
        else:
            self.zero_cmd()
        self.sync_rc_override(reason, armed, keys_on)
        self.show_rc()
 
    def rc_tick(self):
        now = time.monotonic()
        dt = min(now - self.last_tick, 0.2)
        self.last_tick = now
        try:
            self.control_step(dt)
        except Exception as error:
            self.status_var.set("Control loop error: %s" % error)
        self.root.after(RC_TICK_MS, self.rc_tick)
 
        
    # ---------- Map ----------
    def drone_icon(self, heading):
        key = int(round(heading / 5.0) * 5) % 360
        if key not in self.drone_icons:
            self.drone_icons[key] = self.make_drone_icon(key)
        return self.drone_icons[key]

    def update_map(self):
        if self.vehicle is None:
            return
        loc = self.vehicle.location.global_frame
        if not loc.lat or not loc.lon:
            return
        lat, lon = loc.lat, loc.lon
        self.last_position = (lat, lon)
        icon = self.drone_icon(self.vehicle.heading or 0)

        if self.drone_marker is None:
            self.drone_marker = self.map_view.set_marker(lat, lon, icon=icon)
        else:
            self.drone_marker.set_position(lat, lon)
            self.drone_marker.change_icon(icon)

        if not self.map_centered:
            self.map_centered = True
            self.center_on_drone()
            self.map_view.set_zoom(MAP_FOLLOW_ZOOM)

        if not self.trail or self.distance_m(*self.trail[-1], lat, lon) >= TRAIL_STEP_M:
            self.trail.append((lat, lon))
            del self.trail[:-TRAIL_MAX_POINTS]
            if len(self.trail) >= 2:
                if self.trail_path is None:
                    self.trail_path = self.map_view.set_path(list(self.trail), color="#00e5ff", width=3)
                else:
                    self.trail_path.set_position_list(list(self.trail))

        self.update_target_text()

    def map_tick(self):
        try:
            self.update_map()
        except Exception as error:
            self.status_var.set("Map error: %s" % error)
        self.root.after(MAP_REFRESH_MS, self.map_tick)

    def center_on_drone(self):
        if self.last_position is None:
            self.status_var.set("No GPS position yet")
            return
        self.map_view.set_position(*self.last_position)

    def reset_map(self):
        for item in (self.drone_marker, self.trail_path):
            if item is not None:
                item.delete()
        self.drone_marker = None
        self.trail_path = None
        self.trail = []
        self.last_position = None
        self.map_centered = False
        self.update_target_text()

    def on_map_click(self, coords):
        self.set_target(coords[0], coords[1])

    def set_target(self, lat, lon):
        self.target = (lat, lon)
        if self.target_marker is None:
            self.target_marker = self.map_view.set_marker(lat, lon, text="Target")
        else:
            self.target_marker.set_position(lat, lon)
        self.update_target_text()

    def clear_target(self):
        if self.target_marker is not None:
            self.target_marker.delete()
            self.target_marker = None
        self.target = None
        self.update_target_text()

    def update_target_text(self):
        if self.target is None:
            self.target_var.set("Target: none (click the map)")
        elif self.last_position is None:
            self.target_var.set("Target: %.6f, %.6f" % self.target)
        else:
            self.target_var.set("Target: %.0f m away" % self.distance_m(*self.last_position, *self.target))

    def on_map_fly_here(self, coords):
        self.set_target(coords[0], coords[1])
        self.fly_to_target()

    def fly_to_target(self):
        if self.vehicle is None or self.target is None:
            return
        try:
            altitude = float(self.alt_var.get())
            if altitude <= 0:
                raise ValueError
        except ValueError:
            self.status_var.set("Enter a flight altitude in meters (a number above 0)")
            return
        if not self.vehicle.armed:
            return
        if (self.vehicle.location.global_relative_frame.alt or 0) < MIN_FLY_ALT_M:
            return
        lat, lon = self.target
        self.vehicle.mode = VehicleMode("GUIDED")
        self.vehicle.simple_goto(LocationGlobalRelative(lat, lon, altitude))
        self.status_var.set("Flying to the target")


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
            vehicle = connect(conn_string, wait_ready=True, timeout=30, rate=TELEMETRY_RATE_HZ)
            self.results.put(("connected", vehicle))
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
        for message in ("RC_CHANNELS", "RC_CHANNELS_RAW"):
            vehicle.add_message_listener(message, self.on_rc_message)
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
        self.rc_state_var.set("N/A")
        self.set_connected_ui(False)
        self.status_var.set("Not connected")
        self.connection_status.set("Disconnected")
        self.mode_status.set("N/A")
        self.arm_status.set("N/A")
        self.altitude_status.set("N/A")
        self.heading_status.set("N/A")
        self.clear_keys()
        self.zero_cmd()
        self.rc_values = {name: None for name in RC_CHANNEL}
        self.show_rc()
        self.reset_map()
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