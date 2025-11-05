# lib/aimbot.py — full updated file
# - Notebook UI, withdraw-on-close
# - Model selector (.pt/.onnx in lib/)
# - Auto-init mouse backend even if saved selection equals attribute but backend not initialized
# - Reinitialize backend button
# - Activator options include Shift (VK 0x10) and capture supports Shift
# - Confidence control restored
# - GUI hotkey polling and hold/toggle activation preserved

import ctypes
import cv2
import json
import math
import mss
import os
import sys
import time
import torch
import numpy as np
import win32api
import tkinter as tk
from tkinter import ttk
from termcolor import colored
from ultralytics import YOLO
from pynput.mouse import Button as PynputMouseButton
from pynput.keyboard import Key, KeyCode

# Auto Screen Resolution
screensize = {'X': ctypes.windll.user32.GetSystemMetrics(0), 'Y': ctypes.windll.user32.GetSystemMetrics(1)}
screen_res_x = screensize['X']
screen_res_y = screensize['Y']
screen_x = int(screen_res_x / 2)
screen_y = int(screen_res_y / 2)

# defaults
DEFAULT_FOV = 210
DEFAULT_CONFIDENCE = 0.45
DEFAULT_USE_TRIGGER = False

mouse_methods = ['win32', 'ddxoft', 'arduino']
mouse_method = mouse_methods[1]

ARDUINO_PORT = os.environ.get("ARDUINO_PORT", "COM6")
ARDUINO_BAUD = 115200

PUL = ctypes.POINTER(ctypes.c_ulong)

# mapping mouse names and modifier keys to virtual-key codes (VK)
MOUSE_VK = {
    "Mouse Left": 0x01,
    "Mouse Right": 0x02,
    "Mouse Middle": 0x04,
    "Mouse X1": 0x05,
    "Mouse X2": 0x06,
}

# include Shift as activator option -> VK 0x10
SPECIAL_KEY_VK = {
    "Shift": 0x10,
    "Ctrl": 0x11,
    "Alt": 0x12,
}

PYBUTTON_TO_NAME = {
    PynputMouseButton.left: "Mouse Left",
    PynputMouseButton.right: "Mouse Right",
    PynputMouseButton.middle: "Mouse Middle",
    PynputMouseButton.x1: "Mouse X1",
    PynputMouseButton.x2: "Mouse X2",
}

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong),
                ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]

class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput),
                ("mi", MouseInput),
                ("hi", HardwareInput)]

class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong),
                ("ii", Input_I)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class Aimbot:
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    screen = mss.mss()
    pixel_increment = 1

    CONFIG_PATH = "lib/config/config.json"
    try:
        with open(CONFIG_PATH) as f:
            sens_config = json.load(f)
    except Exception:
        sens_config = {}

    aimbot_status = colored("DISABLED", 'red')
    mouse_dll = None
    arduino_serial = None
    instance = None

    def __init__(self, box_constant=DEFAULT_FOV, collect_data=False, mouse_delay=0.0009):
        # detection box size
        self.box_constant = int(self.sens_config.get("fov", box_constant))

        # model path and model object
        self.model_path = self.sens_config.get("model_path", "lib/AIOv11.onnx")
        self.model = None
        self._model_loaded_path = None
        self._load_model(self.model_path)

        print("[INFO] Loading the neural network model (may have been loaded above)")
        if torch.cuda.is_available():
            print(colored("CUDA ACCELERATION [ENABLED]", "green"))
        else:
            print(colored("[!] CUDA ACCELERATION IS UNAVAILABLE", "red"))
            print(colored("[!] Check your PyTorch installation, else performance will be poor", "red"))

        self.conf = float(self.sens_config.get("confidence", DEFAULT_CONFIDENCE))
        self.iou = 0.45
        self.collect_data = collect_data
        self.mouse_delay = mouse_delay

        # mouse backend string loaded from config; _set_mouse_method will initialize
        self.mouse_method = str(self.sens_config.get("mouse_method", mouse_method)).lower()

        # UI enabler
        self.aimbot_ui_enabler = bool(self.sens_config.get("aimbot_enabled", True))
        if self.aimbot_ui_enabler:
            Aimbot.aimbot_status = colored("ENABLED", 'green')
        else:
            Aimbot.aimbot_status = colored("DISABLED", 'red')

        # EMA smoothing
        self.ema_enabled = bool(self.sens_config.get("ema_enabled", 0))
        self.ema_alpha = float(self.sens_config.get("ema_amount", 1.0))
        if self.ema_alpha <= 0.0:
            self.ema_alpha = 0.01
        if self.ema_alpha > 1.0:
            self.ema_alpha = 1.0
        self._ema_x = None
        self._ema_y = None

        # sensitivities
        self.xy_sens = float(self.sens_config.get("xy_sens", 5.0))
        self.targeting_sens = float(self.sens_config.get("targeting_sens", 100.0))
        self._recompute_scales()

        # aim height configurable
        self.aim_height = int(self.sens_config.get("aim_height", 6))
        if self.aim_height <= 0:
            self.aim_height = 6

        # activator (what must be held or pressed to activate). Format:
        # {"type":"mouse"|"key", "code":<vk_int>, "name":<str>}
        self.activator = self.sens_config.get("activator", {"type": "mouse", "code": MOUSE_VK.get("Mouse X1", 0x05), "name": "Mouse X1"})
        # activation mode: 'hold' or 'toggle'
        self.activation_mode = self.sens_config.get("activation_mode", "hold")
        # capture flags
        self._capture_activator = False
        self._capture_gui_hotkey = False

        # GUI root + var refs (created lazily)
        self._gui_root = None
        self._tk_vars = {}

        # GUI hotkey default (F3 = 0x72). Stored in sens_config as dict similar to activator
        self.gui_hotkey = self.sens_config.get("gui_hotkey", {"type": "key", "code": 0x72, "name": "F3"})
        # previous GUI hotkey down state to detect edges
        self._last_gui_hotkey_down = False

        # normalize and initialize mouse method (may open dll/serial)
        # ensure initialization attempt even if saved mouse_method equals attribute but backend not initialized
        self._set_mouse_method(self.mouse_method, force=False)

        print("\n[INFO] Use the GUI to enable the aimbot (UI enabler), choose activator and activation mode.")
        print("[INFO] Default GUI hotkey is F3 to show/hide the control window. Press F2 to quit.")

    # -----------------------
    # Model handling
    # -----------------------
    def list_models_in_lib(self):
        lib_dir = "lib"
        try:
            files = os.listdir(lib_dir)
        except Exception:
            return []
        models = [os.path.join(lib_dir, f) for f in files if f.lower().endswith(('.pt', '.onnx'))]
        models.sort()
        return models

    def _load_model(self, path):
        """
        Load a YOLO model (onnx or pt). Avoid re-loading the same path repeatedly.
        """
        if path is None:
            return False
        try:
            path = str(path)
            if self._model_loaded_path == path and self.model is not None:
                # already loaded
                return True
            if not os.path.exists(path):
                print(colored(f"[WARN] Model file not found: {path}", "yellow"))
                return False
            print(f"[INFO] Loading model: {path}")
            try:
                # attempt to load model via ultralytics YOLO wrapper
                self.model = YOLO(path, task='detect')
                self._model_loaded_path = path
                self.model_path = path
                self.sens_config["model_path"] = path
                print(colored(f"[INFO] Model loaded: {path}", "green"))
                return True
            except Exception as e:
                print(colored(f"[ERROR] Failed to load model '{path}': {e}", "red"))
                # leave previous model intact if exist
                return False
        except Exception as e:
            print(colored(f"[ERROR] _load_model exception: {e}", "red"))
            return False

    # -----------------------
    # Mouse backend handling
    # -----------------------
    def _set_mouse_method(self, method_name, force=False):
        """
        Set/switch mouse movement backend at runtime: 'win32', 'ddxoft', 'arduino'.
        Attempts to initialize the chosen backend. If the method did not change and
        force is False, this function returns quickly (avoids repeated init spam) unless the
        underlying backend isn't initialized — in that case it will attempt init.
        """
        try:
            m = str(method_name).lower()
        except Exception:
            m = 'win32'
        if m not in mouse_methods:
            m = 'win32'

        # If same and not forcing, check if backend actually initialized; if yes, skip.
        current = getattr(self, "mouse_method", None)
        if not force and current == m:
            if m == 'ddxoft' and Aimbot.mouse_dll is not None:
                return
            if m == 'arduino' and Aimbot.arduino_serial is not None and getattr(Aimbot.arduino_serial, "is_open", False):
                return
            if m == 'win32':
                return
            # else fall through and attempt initialization

        prev = getattr(self, "mouse_method", None)
        self.mouse_method = m
        self.sens_config["mouse_method"] = self.mouse_method

        # Clear existing handles only when switching
        if prev != m:
            # close Arduino if open
            try:
                if Aimbot.arduino_serial is not None:
                    try:
                        if getattr(Aimbot.arduino_serial, "is_open", False):
                            Aimbot.arduino_serial.close()
                    except Exception:
                        pass
                    Aimbot.arduino_serial = None
            except Exception:
                pass
            # unload ddxoft dll handle (no proper unload, just drop ref)
            Aimbot.mouse_dll = None

        # Initialize requested backend:
        if m == 'ddxoft':
            # If already loaded, skip
            if Aimbot.mouse_dll is not None:
                print(colored("ddxoft already initialized.", "green"))
                return
            dll_path = os.path.abspath("lib/mouse/dd40605x64.dll")
            if os.path.exists(dll_path):
                try:
                    Aimbot.mouse_dll = ctypes.WinDLL(dll_path)
                    time.sleep(0.2)
                    Aimbot.mouse_dll.DD_btn.argtypes = [ctypes.c_int]
                    Aimbot.mouse_dll.DD_btn.restype = ctypes.c_int
                    init = Aimbot.mouse_dll.DD_btn(0)
                    if init == 1:
                        print(colored('Loaded ddxoft successfully!', 'green'))
                    else:
                        print(colored('ddxoft initialization failed. Defaulting to Win32', 'yellow'))
                        self.mouse_method = 'win32'
                        Aimbot.mouse_dll = None
                except Exception as e:
                    print(colored(f'[WARN] ddxoft init failed: {e}. Falling back to win32', 'yellow'))
                    self.mouse_method = 'win32'
                    Aimbot.mouse_dll = None
            else:
                print(colored('ddxoft DLL not found. Defaulting to Win32', 'yellow'))
                self.mouse_method = 'win32'
        elif m == 'arduino':
            # If already open, skip
            try:
                if Aimbot.arduino_serial is not None and getattr(Aimbot.arduino_serial, "is_open", False):
                    print(colored("Arduino serial already open.", "green"))
                    return
            except Exception:
                pass
            try:
                import serial
                port = getattr(self, "ARDUINO_PORT", ARDUINO_PORT)
                baud = getattr(self, "ARDUINO_BAUD", ARDUINO_BAUD)
                print(f"[INFO] Attempting to open Arduino serial at {port} @{baud}...")
                Aimbot.arduino_serial = serial.Serial(port=port, baudrate=baud, timeout=0.1)
                time.sleep(2.0)
                try:
                    Aimbot.arduino_serial.reset_input_buffer()
                    Aimbot.arduino_serial.reset_output_buffer()
                except Exception:
                    pass
                print(colored(f"[INFO] Arduino serial opened on {port}", "green"))
            except Exception as e:
                print(colored(f"[WARN] Failed to open Arduino serial: {e}. Defaulting to Win32.", "yellow"))
                self.mouse_method = 'win32'
                Aimbot.arduino_serial = None
        else:
            # win32: nothing to initialize, ensure handles are None
            Aimbot.mouse_dll = None
            Aimbot.arduino_serial = None

        # persist choice
        self._save_config()

    # -----------------------
    # Utility helpers
    # -----------------------
    def _recompute_scales(self):
        try:
            self.xy_scale = 10.0 / max(0.0001, float(self.xy_sens))
            self.targeting_scale = 1000.0 / (max(0.0001, float(self.targeting_sens)) * max(0.0001, float(self.xy_sens)))
            self.sens_config["xy_sens"] = float(self.xy_sens)
            self.sens_config["targeting_sens"] = float(self.targeting_sens)
            self.sens_config["xy_scale"] = float(self.xy_scale)
            self.sens_config["targeting_scale"] = float(self.targeting_scale)
        except Exception:
            pass

    def _save_config(self):
        try:
            dir_path = os.path.dirname(self.CONFIG_PATH)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)
            # make sure model_path is up to date
            if getattr(self, "_model_loaded_path", None):
                self.sens_config["model_path"] = self._model_loaded_path
            Aimbot.sens_config.update(self.sens_config)
            Aimbot.sens_config["activator"] = self.activator
            Aimbot.sens_config["activation_mode"] = self.activation_mode
            Aimbot.sens_config["gui_hotkey"] = self.gui_hotkey
            Aimbot.sens_config["aimbot_enabled"] = int(self.aimbot_ui_enabler)
            Aimbot.sens_config["aim_height"] = int(self.aim_height)
            Aimbot.sens_config["mouse_method"] = str(self.mouse_method)
            Aimbot.sens_config["confidence"] = float(self.conf)
            with open(self.CONFIG_PATH, 'w') as wf:
                json.dump(Aimbot.sens_config, wf, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to save config: {e}")

    # -----------------------
    # Input/capture handlers
    # -----------------------
    def handle_mouse_event(self, x, y, button, pressed):
        # capture only reacts to press
        if pressed:
            # capture activator
            if self._capture_activator:
                name = PYBUTTON_TO_NAME.get(button, None)
                if name is not None and name in MOUSE_VK:
                    code = MOUSE_VK[name]
                    self.activator = {"type": "mouse", "code": int(code), "name": name}
                    self.sens_config["activator"] = self.activator
                    self._capture_activator = False
                    self._save_config()
                    print(f"[INFO] Activator captured: {name} (mouse).")
                    return

            # toggle-mode: if activation_mode == 'toggle', check if this button matches activator and toggle runtime activation
            if self.activation_mode == "toggle":
                # only respond if UI enabler is ON
                if self.sens_config.get("aimbot_enabled", True):
                    name = PYBUTTON_TO_NAME.get(button, None)
                    if name is not None:
                        vk = MOUSE_VK.get(name)
                        if vk is not None and self.activator.get("type") == "mouse" and int(self.activator.get("code", 0)) == vk:
                            self._toggle_runtime_activation()
                            return

    def handle_key_event(self, key, pressed):
        """
        Called from the global keyboard listener (lunar.py). Handles:
         - activator capture when _capture_activator True
         - gui hotkey capture when _capture_gui_hotkey True
         - toggle-mode activation (activation_mode == 'toggle') by comparing pressed key's vk to activator
         - GUI show/hide when gui hotkey is pressed (we also poll for edge in main loop; capture here still stores)
        """
        # We only handle on press for capture/toggles
        if not pressed:
            return

        # First: handle GUI-hotkey capture
        if self._capture_gui_hotkey:
            vk_code = None
            name = None
            if isinstance(key, KeyCode):
                try:
                    ch = key.char
                    if ch:
                        # map printable char to VK using VkKeyScan for more accuracy on Windows
                        try:
                            vk_scan = win32api.VkKeyScan(ch)
                            if vk_scan != -1:
                                vk_code = vk_scan & 0xFF
                            else:
                                vk_code = ord(ch.upper())
                        except Exception:
                            vk_code = ord(ch.upper())
                        name = f"Key '{ch.upper()}'"
                except Exception:
                    pass
            else:
                try:
                    vk_code = getattr(key, "vk", None)
                    special_map = {
                        Key.space: 0x20, Key.enter: 0x0D, Key.esc: 0x1B, Key.tab: 0x09,
                        Key.shift: 0x10, Key.shift_r: 0x10, Key.shift_l: 0x10,
                        Key.ctrl: 0x11, Key.ctrl_l: 0x11, Key.ctrl_r: 0x11,
                        Key.alt: 0x12, Key.alt_l: 0x12, Key.alt_r: 0x12,
                        Key.up: 0x26, Key.down: 0x28, Key.left: 0x25, Key.right: 0x27,
                        Key.f1: 0x70, Key.f2: 0x71, Key.f3: 0x72, Key.f4: 0x73, Key.f5: 0x74,
                        Key.insert: 0x2D, Key.delete: 0x2E, Key.home: 0x24, Key.end: 0x23,
                        Key.page_up: 0x21, Key.page_down: 0x22,
                    }
                    if vk_code is None and key in special_map:
                        vk_code = special_map[key]
                        name = str(key)
                except Exception:
                    vk_code = None
            if vk_code is not None:
                if name is None:
                    try:
                        if isinstance(key, KeyCode) and key.char:
                            name = f"Key '{key.char.upper()}'"
                        else:
                            name = str(key)
                    except Exception:
                        name = str(vk_code)
                self.gui_hotkey = {"type": "key", "code": int(vk_code), "name": name}
                self.sens_config["gui_hotkey"] = self.gui_hotkey
                self._capture_gui_hotkey = False
                self._save_config()
                print(f"[INFO] GUI hotkey captured: {name} (vk={vk_code}). Use this to show/hide the UI.")
                return
            else:
                print("[WARN] Could not map pressed key to a VK code for GUI hotkey capture.")
                self._capture_gui_hotkey = False
                return

        # Next: handle activator capture
        if self._capture_activator:
            vk_code = None
            name = None
            if isinstance(key, KeyCode):
                try:
                    ch = key.char
                    if ch:
                        try:
                            vk_scan = win32api.VkKeyScan(ch)
                            if vk_scan != -1:
                                vk_code = vk_scan & 0xFF
                            else:
                                vk_code = ord(ch.upper())
                        except Exception:
                            vk_code = ord(ch.upper())
                        name = f"Key '{ch.upper()}'"
                except Exception:
                    pass
            else:
                try:
                    vk_code = getattr(key, "vk", None)
                    special_map = {
                        Key.space: 0x20, Key.enter: 0x0D, Key.esc: 0x1B, Key.tab: 0x09,
                        Key.shift: 0x10, Key.shift_r: 0x10, Key.shift_l: 0x10,
                        Key.ctrl: 0x11, Key.ctrl_l: 0x11, Key.ctrl_r: 0x11,
                        Key.alt: 0x12, Key.alt_l: 0x12, Key.alt_r: 0x12,
                        Key.up: 0x26, Key.down: 0x28, Key.left: 0x25, Key.right: 0x27,
                        Key.f1: 0x70, Key.f2: 0x71, Key.f3: 0x72, Key.f4: 0x73, Key.f5: 0x74,
                        Key.insert: 0x2D, Key.delete: 0x2E, Key.home: 0x24, Key.end: 0x23,
                        Key.page_up: 0x21, Key.page_down: 0x22,
                    }
                    if vk_code is None and key in special_map:
                        vk_code = special_map[key]
                        name = str(key)
                except Exception:
                    vk_code = None
            if vk_code is not None:
                if name is None:
                    try:
                        if isinstance(key, KeyCode) and key.char:
                            name = f"Key '{key.char.upper()}'"
                        else:
                            name = str(key)
                    except Exception:
                        name = str(vk_code)
                self.activator = {"type": "key", "code": int(vk_code), "name": name}
                self.sens_config["activator"] = self.activator
                self._capture_activator = False
                self._save_config()
                print(f"[INFO] Activator captured: {name} (vk={vk_code}). Hold or press to activate based on mode.")
                return
            else:
                print("[WARN] Could not determine VK code for pressed key during activator capture.")
                self._capture_activator = False
                return

        # Next: check for toggle-mode activation handling and GUI hotkey handled by polling in main loop
        # Toggle handling (press activator -> toggle runtime)
        if self.activation_mode == "toggle":
            if self.sens_config.get("aimbot_enabled", True):
                act = self.activator
                if act and act.get("type") == "key":
                    try:
                        # get vk code for this key press
                        k_vk = getattr(key, "vk", None)
                        if k_vk is None and isinstance(key, KeyCode):
                            try:
                                ch = key.char
                                if ch:
                                    vk_scan = win32api.VkKeyScan(ch)
                                    if vk_scan != -1:
                                        k_vk = vk_scan & 0xFF
                                    else:
                                        k_vk = ord(ch.upper())
                            except Exception:
                                k_vk = None
                        if k_vk is not None and int(act.get("code", 0)) == int(k_vk):
                            self._toggle_runtime_activation()
                            return
                    except Exception:
                        pass

    def _toggle_runtime_activation(self):
        # toggle runtime visible status
        if Aimbot.aimbot_status == colored("ENABLED", 'green'):
            Aimbot.aimbot_status = colored("DISABLED", 'red')
        else:
            Aimbot.aimbot_status = colored("ENABLED", 'green')
        sys.stdout.write("\033[K")
        print(f"[!] AIMBOT RUNTIME STATUS [{Aimbot.aimbot_status}]", end="\r")

    def set_aimbot_ui_enabler(self, enabled: bool):
        """
        Toggle the UI enabler. When disabled, holding the activator will NOT activate aiming.
        Persist to config.
        """
        self.aimbot_ui_enabler = bool(enabled)
        self.sens_config["aimbot_enabled"] = int(self.aimbot_ui_enabler)
        Aimbot.sens_config.update(self.sens_config)
        if self.aimbot_ui_enabler:
            Aimbot.aimbot_status = colored("ENABLED", 'green')
        else:
            Aimbot.aimbot_status = colored("DISABLED", 'red')
        self._save_config()
        sys.stdout.write("\033[K")
        print(f"[!] Aimbot UI enabler set to [{'ON' if self.aimbot_ui_enabler else 'OFF'}]", end="\r")

    def left_click(self):
        if self.mouse_method == 'ddxoft' and Aimbot.mouse_dll is not None:
            try:
                Aimbot.mouse_dll.DD_btn(1)
                self.sleep(0.001)
                Aimbot.mouse_dll.DD_btn(2)
            except Exception:
                pass
        elif self.mouse_method == 'win32':
            ctypes.windll.user32.mouse_event(0x0002)
            self.sleep(0.0001)
            ctypes.windll.user32.mouse_event(0x0004)
        elif self.mouse_method == 'arduino':
            try:
                if Aimbot.arduino_serial and Aimbot.arduino_serial.is_open:
                    Aimbot.arduino_serial.write(b"0,0,1\n")
                    Aimbot.arduino_serial.flush()
                    self.sleep(0.001)
            except Exception:
                ctypes.windll.user32.mouse_event(0x0002)
                self.sleep(0.0001)
                ctypes.windll.user32.mouse_event(0x0004)

    def sleep(self, duration, get_now=time.perf_counter):
        if duration == 0:
            return
        now = get_now()
        end = now + duration
        while now < end:
            now = get_now()

    def is_aimbot_enabled(self):
        """
        runtime status label: we show ENABLED if class-level aimbot_status is enabled.
        """
        return (Aimbot.aimbot_status == colored("ENABLED", 'green'))

    def is_shooting(self):
        return win32api.GetKeyState(0x01) in (-127, -128)

    def is_target_locked(self, x, y):
        threshold = 5
        return screen_x - threshold <= x <= screen_x + threshold and screen_y - threshold <= y <= screen_y + threshold

    def is_activator_held(self):
        """
        Check whether the currently configured activator is currently held (pressed).
        Uses GetAsyncKeyState for real-time detection.
        """
        try:
            act = self.activator
            if not act:
                return False
            vk = int(act.get("code", 0))
            return bool(win32api.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _send_arduino_move(self, rel_x, rel_y):
        try:
            if Aimbot.arduino_serial and Aimbot.arduino_serial.is_open:
                cmd = f"{int(rel_x)},{int(rel_y)},0\n".encode("ascii")
                Aimbot.arduino_serial.write(cmd)
                Aimbot.arduino_serial.flush()
        except Exception:
            pass

    def move_crosshair(self, x, y):
        scale = self.targeting_scale
        for rel_x, rel_y in self.interpolate_coordinates_from_center((x, y), scale):
            if self.mouse_method == 'ddxoft' and Aimbot.mouse_dll is not None:
                try:
                    Aimbot.mouse_dll.DD_movR(rel_x, rel_y)
                except Exception:
                    pass
            elif self.mouse_method == 'win32':
                Aimbot.ii_.mi = MouseInput(rel_x, rel_y, 0, 0x0001, 0, ctypes.pointer(Aimbot.extra))
                input_obj = Input(ctypes.c_ulong(0), Aimbot.ii_)
                ctypes.windll.user32.SendInput(1, ctypes.byref(input_obj), ctypes.sizeof(input_obj))
            elif self.mouse_method == 'arduino':
                self._send_arduino_move(rel_x, rel_y)
            self.sleep(self.mouse_delay)

    def interpolate_coordinates_from_center(self, absolute_coordinates, scale):
        diff_x = (absolute_coordinates[0] - screen_x) * scale / Aimbot.pixel_increment
        diff_y = (absolute_coordinates[1] - screen_y) * scale / Aimbot.pixel_increment
        length = int(math.dist((0, 0), (diff_x, diff_y)))
        if length == 0:
            return
        unit_x = (diff_x / length) * Aimbot.pixel_increment
        unit_y = (diff_y / length) * Aimbot.pixel_increment
        x = y = sum_x = sum_y = 0
        for k in range(0, length):
            sum_x += x
            sum_y += y
            x, y = round(unit_x * k - sum_x), round(unit_y * k - sum_y)
            yield x, y

    # -----------------------
    # GUI creation/updating
    # -----------------------
    def _create_gui(self):
        if self._gui_root is not None:
            return

        root = tk.Tk()
        # intentionally no title per request
        root.geometry("560x780")
        root.minsize(420, 300)
        root.resizable(True, True)

        # Use Notebook to organize controls into tabs so the window is shorter
        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Model tab
        model_frame = ttk.Frame(notebook, padding=6)
        notebook.add(model_frame, text="Model")

        tk.Label(model_frame, text="Model (.pt or .onnx in lib/)").pack(anchor='w')
        models = self.list_models_in_lib()
        model_names = [os.path.basename(p) for p in models] if models else ["(no models found)"]
        model_var = tk.StringVar(value=os.path.basename(self._model_loaded_path) if self._model_loaded_path else os.path.basename(self.model_path))
        model_cb = ttk.Combobox(model_frame, textvariable=model_var, values=model_names, state="readonly")
        model_cb.pack(fill='x', pady=(4,2))
        refresh_btn = ttk.Button(model_frame, text="Refresh models", command=lambda: self._refresh_model_list(model_cb))
        refresh_btn.pack(fill='x', pady=(2,4))

        # General tab
        general_frame = ttk.Frame(notebook, padding=6)
        notebook.add(general_frame, text="General")

        tk.Label(general_frame, text="Aimbot UI Enabler (must be ON for activator to enable aiming)").pack(anchor='w')
        aim_var = tk.IntVar(value=1 if self.aimbot_ui_enabler else 0)
        chk_aim = ttk.Checkbutton(general_frame, text="Enable Aimbot (UI)", variable=aim_var)
        chk_aim.pack(anchor='w', pady=(2,6))

        tk.Label(general_frame, text="Activation Mode").pack(anchor='w')
        activation_var = tk.StringVar(value=self.activation_mode)
        rb_hold = ttk.Radiobutton(general_frame, text="Hold (hold activator to aim)", variable=activation_var, value="hold")
        rb_toggle = ttk.Radiobutton(general_frame, text="Toggle (press activator to toggle aiming)", variable=activation_var, value="toggle")
        rb_hold.pack(anchor='w')
        rb_toggle.pack(anchor='w', pady=(0,6))

        tk.Label(general_frame, text="Activator (capture to set)").pack(anchor='w')
        activator_name = tk.StringVar(value=self.activator.get("name", "Mouse X1"))
        activator_lbl = tk.Label(general_frame, textvariable=activator_name, font=("TkDefaultFont", 10, "bold"))
        activator_lbl.pack(anchor='w', pady=(2,2))
        activator_options = ["Mouse Left", "Mouse Right", "Mouse Middle", "Mouse X1", "Mouse X2", "Shift", "Keyboard (Capture)"]
        activator_cb_var = tk.StringVar(value=self.activator.get("name", "Mouse X1"))
        activator_cb = ttk.Combobox(general_frame, textvariable=activator_cb_var, values=activator_options, state="readonly")
        activator_cb.pack(fill='x', pady=(2,4))
        capture_btn = ttk.Button(general_frame, text="Capture Activator (press desired key/button)", command=lambda: self._start_capture_activator(activator_cb_var))
        capture_btn.pack(fill='x', pady=(2,6))

        tk.Label(general_frame, text="GUI Hotkey (press to show/hide UI)").pack(anchor='w')
        gui_hotkey_name = tk.StringVar(value=self.gui_hotkey.get("name", "F3"))
        gui_hotkey_lbl = tk.Label(general_frame, textvariable=gui_hotkey_name, font=("TkDefaultFont", 10, "bold"))
        gui_hotkey_lbl.pack(anchor='w', pady=(2,2))
        capture_gui_btn = ttk.Button(general_frame, text="Capture GUI Hotkey", command=self._start_capture_gui_hotkey)
        capture_gui_btn.pack(fill='x', pady=(2,6))

        # Aim tab
        aim_frame = ttk.Frame(notebook, padding=6)
        notebook.add(aim_frame, text="Aim")

        tk.Label(aim_frame, text="FOV (detection box size)").pack(anchor='w')
        fov_var = tk.IntVar(value=int(self.box_constant))
        s_fov = ttk.Scale(aim_frame, from_=50, to=800, orient='horizontal', variable=fov_var)
        s_fov.pack(fill='x', pady=(2,2))
        fov_val_lbl = tk.Label(aim_frame, text=str(int(self.box_constant)))
        fov_val_lbl.pack(anchor='e', pady=(0,6))

        # Confidence control
        tk.Label(aim_frame, text="Confidence (detection threshold)").pack(anchor='w')
        conf_var = tk.DoubleVar(value=self.conf)
        s_conf = ttk.Scale(aim_frame, from_=0.01, to=1.0, orient='horizontal', variable=conf_var)
        s_conf.pack(fill='x', pady=(2,2))
        conf_val_lbl = tk.Label(aim_frame, text=f"{self.conf:.2f}")
        conf_val_lbl.pack(anchor='e', pady=(0,6))

        tk.Label(aim_frame, text="Aim Height (lower = higher aim)").pack(anchor='w')
        aim_height_var = tk.IntVar(value=int(self.aim_height))
        s_aim_height = ttk.Scale(aim_frame, from_=2, to=100, orient='horizontal', variable=aim_height_var)
        s_aim_height.pack(fill='x', pady=(2,2))
        aim_height_val_lbl = tk.Label(aim_frame, text=str(self.aim_height))
        aim_height_val_lbl.pack(anchor='e', pady=(0,6))

        tk.Label(aim_frame, text="EMA Smoothing").pack(anchor='w')
        ema_var = tk.IntVar(value=1 if self.ema_enabled else 0)
        chk_ema = ttk.Checkbutton(aim_frame, text="Enable EMA", variable=ema_var)
        chk_ema.pack(anchor='w', pady=(2,2))
        tk.Label(aim_frame, text="EMA Alpha (higher = less smoothing)").pack(anchor='w')
        ema_alpha_var = tk.DoubleVar(value=self.ema_alpha)
        s_ema_alpha = ttk.Scale(aim_frame, from_=0.01, to=1.0, orient='horizontal', variable=ema_alpha_var)
        s_ema_alpha.pack(fill='x', pady=(2,2))
        ema_val_lbl = tk.Label(aim_frame, text=f"{self.ema_alpha:.2f}")
        ema_val_lbl.pack(anchor='e', pady=(0,6))

        # Sensitivity tab
        sens_frame = ttk.Frame(notebook, padding=6)
        notebook.add(sens_frame, text="Sensitivity")

        tk.Label(sens_frame, text="XY Sens (v)").pack(anchor='w')
        xy_var = tk.DoubleVar(value=self.xy_sens)
        s_xy = ttk.Scale(sens_frame, from_=0.1, to=20.0, orient='horizontal', variable=xy_var)
        s_xy.pack(fill='x', pady=(2,2))
        xy_val_lbl = tk.Label(sens_frame, text=f"{self.xy_sens:.2f}")
        xy_val_lbl.pack(anchor='e', pady=(0,6))

        tk.Label(sens_frame, text="Targeting Sens").pack(anchor='w')
        targeting_var = tk.DoubleVar(value=self.targeting_sens)
        s_target = ttk.Scale(sens_frame, from_=1.0, to=1000.0, orient='horizontal', variable=targeting_var)
        s_target.pack(fill='x', pady=(2,2))
        targ_val_lbl = tk.Label(sens_frame, text=f"{self.targeting_sens:.1f}")
        targ_val_lbl.pack(anchor='e', pady=(0,6))

        # Mouse/Backend tab
        mouse_frame = ttk.Frame(notebook, padding=6)
        notebook.add(mouse_frame, text="Mouse/Backend")

        tk.Label(mouse_frame, text="Mouse movement backend").pack(anchor='w')
        mouse_method_var = tk.StringVar(value=str(self.mouse_method))
        mouse_method_cb = ttk.Combobox(mouse_frame, textvariable=mouse_method_var, values=mouse_methods, state="readonly")
        mouse_method_cb.pack(fill='x', pady=(2,4))
        mouse_method_note = tk.Label(mouse_frame, text="Selecting ddxoft or arduino attempts to initialize that backend.", font=("TkDefaultFont", 8))
        mouse_method_note.pack(anchor='w')

        mouse_status_var = tk.StringVar(value=f"{self.mouse_method} ({'initialized' if (Aimbot.mouse_dll or Aimbot.arduino_serial) else 'not initialized'})")
        mouse_status_lbl = tk.Label(mouse_frame, textvariable=mouse_status_var, fg='blue')
        mouse_status_lbl.pack(anchor='w', pady=(2,4))

        reinit_btn = ttk.Button(mouse_frame, text="Reinitialize mouse backend", command=lambda: self._reinit_backend(mouse_method_var, mouse_status_var))
        reinit_btn.pack(fill='x', pady=(2,4))

        # Save/Close buttons at bottom of notebook
        bottom_frame = ttk.Frame(root)
        bottom_frame.pack(fill='x', padx=6, pady=6)
        save_btn = ttk.Button(bottom_frame, text="Save Config", command=self._save_config)
        save_btn.pack(side='left')
        close_btn = ttk.Button(bottom_frame, text="Hide UI", command=root.withdraw)
        close_btn.pack(side='left', padx=(6,0))

        # set WM_DELETE_WINDOW to withdraw so window can be restored later
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        # store refs
        self._gui_root = root
        self._tk_vars = {
            "model_var": model_var,
            "model_cb": model_cb,
            "aim_var": aim_var,
            "activation_var": activation_var,
            "fov_var": fov_var,
            "fov_val_lbl": fov_val_lbl,
            "conf_var": conf_var,
            "conf_val_lbl": conf_val_lbl,
            "ema_var": ema_var,
            "ema_alpha_var": ema_alpha_var,
            "ema_val_lbl": ema_val_lbl,
            "aim_height_var": aim_height_var,
            "aim_height_val_lbl": aim_height_val_lbl,
            "mouse_method_var": mouse_method_var,
            "mouse_method_cb": mouse_method_cb,
            "mouse_status_var": mouse_status_var,
            "xy_var": xy_var,
            "xy_val_lbl": xy_val_lbl,
            "targeting_var": targeting_var,
            "targ_val_lbl": targ_val_lbl,
            "activator_cb_var": activator_cb_var,
            "activator_name_var": activator_name,
            "gui_hotkey_name_var": gui_hotkey_name,
        }

        # traces and handlers
        aim_var.trace_add("write", lambda *_: self.set_aimbot_ui_enabler(bool(aim_var.get())))
        activation_var.trace_add("write", lambda *_: (setattr(self, "activation_mode", activation_var.get()), self.sens_config.update({"activation_mode": self.activation_mode}), self._save_config()))
        model_var.trace_add("write", lambda *_: self._on_model_selected(model_var, model_cb))
        fov_var.trace_add("write", lambda *_: (setattr(self, "box_constant", max(50, int(fov_var.get()))), self.sens_config.update({"fov": int(self.box_constant)}), self._save_config()))
        conf_var.trace_add("write", lambda *_: (setattr(self, "conf", float(conf_var.get())), self.sens_config.update({"confidence": float(self.conf)}), self._save_config()))
        ema_var.trace_add("write", lambda *_: (setattr(self, "ema_enabled", bool(ema_var.get())), self.sens_config.update({"ema_enabled": int(self.ema_enabled)}), self._save_config()))
        ema_alpha_var.trace_add("write", lambda *_: (setattr(self, "ema_alpha", float(ema_alpha_var.get())), self.sens_config.update({"ema_amount": float(self.ema_alpha)}), self._save_config()))
        aim_height_var.trace_add("write", lambda *_: (setattr(self, "aim_height", max(1, int(aim_height_var.get()))), self.sens_config.update({"aim_height": int(self.aim_height)}), self._save_config()))
        mouse_method_var.trace_add("write", lambda *_: (self._set_mouse_method(mouse_method_var.get(), force=False), mouse_status_var.set(f"{self.mouse_method} ({'initialized' if (Aimbot.mouse_dll or Aimbot.arduino_serial) else 'not initialized'})"), self._save_config()))
        xy_var.trace_add("write", lambda *_: (setattr(self, "xy_sens", float(xy_var.get())), self._recompute_scales(), self.sens_config.update({"xy_sens": float(self.xy_sens), "xy_scale": float(self.xy_scale), "targeting_scale": float(self.targeting_scale)}), self._save_config()))
        targeting_var.trace_add("write", lambda *_: (setattr(self, "targeting_sens", float(targeting_var.get())), self._recompute_scales(), self.sens_config.update({"targeting_sens": float(self.targeting_sens), "xy_scale": float(self.xy_scale), "targeting_scale": float(self.targeting_scale)}), self._save_config()))

    def _reinit_backend(self, mouse_method_var, mouse_status_var):
        sel = mouse_method_var.get()
        self._set_mouse_method(sel, force=True)
        initialized = (Aimbot.mouse_dll is not None) or (Aimbot.arduino_serial is not None)
        mouse_status_var.set(f"{self.mouse_method} ({'initialized' if initialized else 'not initialized'})")
        self._save_config()
        print(f"[INFO] Reinitialized mouse backend -> {self.mouse_method}; initialized={initialized}")

    def _on_model_selected(self, model_var, model_cb):
        sel = model_var.get()
        lib_models = self.list_models_in_lib()
        match = None
        for p in lib_models:
            if os.path.basename(p) == sel:
                match = p
                break
        if match:
            if match != self._model_loaded_path:
                if self._load_model(match):
                    print(f"[INFO] Switched model to {match}")
                    self._save_config()
        else:
            print("[WARN] Selected model not found in lib/. Use Refresh model list.")

    def _refresh_model_list(self, combobox_widget):
        models = self.list_models_in_lib()
        names = [os.path.basename(p) for p in models]
        combobox_widget['values'] = names
        if self._model_loaded_path:
            combobox_widget.set(os.path.basename(self._model_loaded_path))
        elif names:
            combobox_widget.set(names[0])

    # -----------------------
    # Main loop
    # -----------------------
    def start(self):
        print("[INFO] Beginning screen capture")
        if not hasattr(self, "sens_config") or self.sens_config is None:
            self.sens_config = {}
        Aimbot.sens_config.update(self.sens_config)

        window_name = "Screen Capture"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        try:
            self._create_gui()
        except Exception as e:
            print(f"[WARN] Failed to create GUI: {e}")
            self._gui_root = None
            self._tk_vars = {}

        while True:
            start_time = time.perf_counter()

            # GUI update + numeric labels
            if self._gui_root is not None:
                try:
                    self._gui_root.update()
                    tv = self._tk_vars
                    if tv:
                        try:
                            tv["fov_val_lbl"].config(text=str(int(self.box_constant)))
                            tv["conf_val_lbl"].config(text=f"{self.conf:.2f}")
                            tv["ema_val_lbl"].config(text=f"{self.ema_alpha:.2f}")
                            tv["aim_height_val_lbl"].config(text=str(self.aim_height))
                            tv["xy_val_lbl"].config(text=f"{self.xy_sens:.2f}")
                            tv["targ_val_lbl"].config(text=f"{self.targeting_sens:.1f}")
                            tv["activator_name_var"].set(self.activator.get("name","Unknown"))
                            tv["gui_hotkey_name_var"].set(self.gui_hotkey.get("name","F3"))
                        except Exception:
                            pass
                except tk.TclError:
                    self._gui_root = None
                    self._tk_vars = {}

            # Poll GUI hotkey edge with GetAsyncKeyState
            try:
                gh = self.gui_hotkey
                if gh and gh.get("type") == "key":
                    vk = int(gh.get("code", 0))
                    is_down = bool(win32api.GetAsyncKeyState(vk) & 0x8000)
                    if is_down and not self._last_gui_hotkey_down:
                        self._toggle_gui_visibility()
                    self._last_gui_hotkey_down = is_down
            except Exception:
                pass

            # detection box
            half_screen_width = ctypes.windll.user32.GetSystemMetrics(0) / 2
            half_screen_height = ctypes.windll.user32.GetSystemMetrics(1) / 2
            detection_box = {
                'left': int(half_screen_width - int(self.box_constant) // 2),
                'top': int(half_screen_height - int(self.box_constant) // 2),
                'width': int(self.box_constant),
                'height': int(self.box_constant)
            }

            initial_frame = Aimbot.screen.grab(detection_box)
            frame = np.array(initial_frame, dtype=np.uint8)
            if frame is None or frame.size == 0:
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # ensure model is loaded
            if self.model is None:
                # try to load fallback model path if available
                if self.model_path:
                    self._load_model(self.model_path)

            # run detection
            try:
                boxes = self.model.predict(source=frame, verbose=False, conf=self.conf, iou=self.iou, half=True)
                result = boxes[0]
            except Exception as e:
                # model prediction error: show error overlay and continue
                cv2.putText(frame, f"Model error: {e}", (5, 60), cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 0, 255), 2)
                cv2.imshow(window_name, frame)
                if cv2.waitKey(1) & 0xFF == ord('0'):
                    break
                continue

            if len(result.boxes.xyxy) != 0:
                least_crosshair_dist = closest_detection = player_in_frame = False
                for box in result.boxes.xyxy:
                    x1, y1, x2, y2 = map(int, box)
                    height = y2 - y1
                    relative_head_X = int((x1 + x2) / 2)
                    relative_head_Y = int((y1 + y2) / 2 - height / max(1, self.aim_height))
                    own_player = x1 < 15 or (x1 < self.box_constant / 5 and y2 > self.box_constant / 1.2)

                    crosshair_dist = math.dist((relative_head_X, relative_head_Y), (self.box_constant / 2, self.box_constant / 2))
                    if not least_crosshair_dist:
                        least_crosshair_dist = crosshair_dist

                    if crosshair_dist <= least_crosshair_dist and not own_player:
                        least_crosshair_dist = crosshair_dist
                        closest_detection = {"relative_head_X": relative_head_X, "relative_head_Y": relative_head_Y, "x1y1": (x1, y1)}
                    if own_player:
                        own_player = False
                        if not player_in_frame:
                            player_in_frame = True

                if closest_detection:
                    cv2.circle(frame, (closest_detection["relative_head_X"], closest_detection["relative_head_Y"]), 5, (115, 244, 113), -1)
                    cv2.line(frame, (closest_detection["relative_head_X"], closest_detection["relative_head_Y"]), (self.box_constant // 2, self.box_constant // 2), (244, 242, 113), 2)

                    absolute_head_X = closest_detection["relative_head_X"] + detection_box['left']
                    absolute_head_Y = closest_detection["relative_head_Y"] + detection_box['top']
                    x1, y1 = closest_detection["x1y1"]

                    if self.ema_enabled:
                        if self._ema_x is None or self._ema_y is None:
                            self._ema_x = float(absolute_head_X)
                            self._ema_y = float(absolute_head_Y)
                        else:
                            self._ema_x = self.ema_alpha * float(absolute_head_X) + (1.0 - self.ema_alpha) * self._ema_x
                            self._ema_y = self.ema_alpha * float(absolute_head_Y) + (1.0 - self.ema_alpha) * self._ema_y
                        smoothed_X = int(round(self._ema_x))
                        smoothed_Y = int(round(self._ema_y))
                        absolute_head_X, absolute_head_Y = smoothed_X, smoothed_Y

                    if self.is_target_locked(absolute_head_X, absolute_head_Y):
                        if DEFAULT_USE_TRIGGER and not self.is_shooting():
                            self.left_click()
                        cv2.putText(frame, "LOCKED", (x1 + 40, y1), cv2.FONT_HERSHEY_DUPLEX, 0.5, (115, 244, 113), 2)
                    else:
                        cv2.putText(frame, "TARGETING", (x1 + 40, y1), cv2.FONT_HERSHEY_DUPLEX, 0.5, (115, 113, 244), 2)

                    # Activation logic
                    should_move = False
                    if self.activation_mode == "hold":
                        should_move = bool(self.aimbot_ui_enabler and self.is_activator_held())
                    else:
                        should_move = bool(self.aimbot_ui_enabler and self.is_aimbot_enabled())

                    if should_move:
                        self.move_crosshair(absolute_head_X, absolute_head_Y)

            # overlay
            status_lines = [
                f"Aimbot (active): {'YES' if ((self.activation_mode=='hold' and self.aimbot_ui_enabler and self.is_activator_held()) or (self.activation_mode=='toggle' and self.aimbot_ui_enabler and self.is_aimbot_enabled())) else 'NO'}",
                f"Aimbot (UI enabler): {'ON' if self.aimbot_ui_enabler else 'OFF'}",
                f"Activation Mode: {self.activation_mode.upper()}",
                f"Activator: {self.activator.get('name','Unknown')}",
                f"GUI Hotkey: {self.gui_hotkey.get('name','F3')}",
                f"Mouse Method: {self.mouse_method}",
                f"Aim Height: {self.aim_height}",
                f"Model: {os.path.basename(self._model_loaded_path) if self._model_loaded_path else os.path.basename(self.model_path)}",
                f"FOV: {int(self.box_constant)}",
                f"Confidence: {self.conf:.2f}",
                f"EMA: {'ON' if self.ema_enabled else 'OFF'} Alpha: {self.ema_alpha:.2f}",
                f"XY Sens: {self.xy_sens:.2f} (scale {self.xy_scale:.3f})",
                f"Targeting Sens: {self.targeting_sens:.2f} (scale {self.targeting_scale:.3f})"
            ]
            for i, line in enumerate(status_lines):
                cv2.putText(frame, line, (5, 30 + (i + 1) * 18), cv2.FONT_HERSHEY_DUPLEX, 0.5, (200, 200, 50), 1)

            cv2.putText(frame, f"FPS: {int(1 / (time.perf_counter() - start_time))}", (5, 20),
                        cv2.FONT_HERSHEY_DUPLEX, 0.7, (113, 116, 244), 2)

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('0'):
                break
            if key == ord('s'):
                self._save_config()

        cv2.destroyWindow(window_name)

    def _start_capture_activator(self, activator_cb_var):
        self._capture_activator = True
        print("[INFO] Activator capture: press the desired key or mouse button now.")
        try:
            activator_cb_var.set("Keyboard (Capture)")
        except Exception:
            pass

    def _start_capture_gui_hotkey(self):
        self._capture_gui_hotkey = True
        print("[INFO] GUI hotkey capture: press the desired key now.")

    def _toggle_gui_visibility(self):
        if self._gui_root is None:
            try:
                self._create_gui()
                return
            except Exception:
                return
        try:
            if self._gui_root.state() == 'withdrawn':
                self._gui_root.deiconify()
            else:
                self._gui_root.withdraw()
        except Exception:
            pass

    def clean_up():
        print("\n[INFO] F2 WAS PRESSED. QUITTING...")
        try:
            if Aimbot.arduino_serial and Aimbot.arduino_serial.is_open:
                Aimbot.arduino_serial.close()
        except Exception:
            pass
        Aimbot.screen.close()
        os._exit(0)

if __name__ == "__main__":
    print("You are in the wrong directory and are running the wrong file; you must run lunar.py")
