import json
import os
import sys
from pynput import keyboard
from pynput import mouse
from termcolor import colored

def on_release(key):
    try:
        # F2 quits (press release)
        if key == keyboard.Key.f2:
            Aimbot.clean_up()
    except NameError:
        pass

def on_press(key):
    """
    Keyboard press events are forwarded to the Aimbot instance so that
    the GUI capture mode can register a keyboard key as the activator or GUI hotkey,
    and so toggle-mode activation can be handled.
    """
    try:
        if hasattr(Aimbot, "instance") and Aimbot.instance is not None:
            try:
                Aimbot.instance.handle_key_event(key, pressed=True)
            except Exception:
                pass
    except NameError:
        pass

def on_click(x, y, button, pressed):
    """
    Forward mouse click events to the Aimbot instance; used for activator capture and
    toggle-mode activation handling by the instance.
    """
    try:
        if hasattr(Aimbot, "instance") and Aimbot.instance is not None:
            try:
                Aimbot.instance.handle_mouse_event(x, y, button, pressed)
            except Exception:
                pass
    except NameError:
        pass

def main():
    global lunar
    lunar = Aimbot(collect_data = "collect_data" in sys.argv)
    # register instance reference so listeners can forward events
    Aimbot.instance = lunar
    lunar.start()

def setup():
    path = "lib/config"
    if not os.path.exists(path):
        os.makedirs(path)

    print("[INFO] In-game X and Y axis sensitivity should be the same")
    def prompt(str):
        valid_input = False
        while not valid_input:
            try:
                number = float(input(str))
                valid_input = True
            except ValueError:
                print("[!] Invalid Input. Make sure to enter only the number (e.g. 6.9)")
        return number

    xy_sens = prompt("X-Axis and Y-Axis Sensitivity (from in-game settings): ")
    targeting_sens = prompt("Targeting Sensitivity (from in-game settings): ")

    print("[INFO] Your in-game targeting sensitivity must be the same as your scoping sensitivity")
    sensitivity_settings = {"xy_sens": xy_sens, "targeting_sens": targeting_sens, "xy_scale": 10/xy_sens, "targeting_scale": 1000/(targeting_sens * xy_sens)}

    # EMA smoothing options
    print("\n[OPTIONAL] Exponential Moving Average (EMA) smoothing for aim target")
    def prompt_int_choice(prompt_text, valid_choices):
        valid = False
        while not valid:
            try:
                val = int(input(prompt_text))
                if val in valid_choices:
                    valid = True
                else:
                    print(f"[!] Invalid choice. Valid options: {valid_choices}")
            except ValueError:
                print("[!] Invalid Input. Enter an integer.")
        return val

    ema_enabled = prompt_int_choice("Enable EMA smoothing? (1 = enabled, 0 = disabled): ", [0, 1])
    ema_amount = 1.0
    if ema_enabled == 1:
        # ask for EMA alpha (amount)
        valid_alpha = False
        while not valid_alpha:
            try:
                ema_amount = float(input("EMA amount (alpha) between 0.01 and 1.0 (higher = less smoothing, 1.0 = no smoothing): "))
                if 0.01 <= ema_amount <= 1.0:
                    valid_alpha = True
                else:
                    print("[!] Invalid alpha. Enter a number between 0.01 and 1.0")
            except ValueError:
                print("[!] Invalid Input. Make sure to enter a decimal number (e.g. 0.2)")

    # Default activator: Mouse X1 hold
    default_activator = {"type": "mouse", "code": 5, "name": "Mouse X1"}  # VK 0x05 for XBUTTON1

    sensitivity_settings["ema_enabled"] = int(ema_enabled)
    sensitivity_settings["ema_amount"] = float(ema_amount)
    sensitivity_settings["aimbot_enabled"] = 1  # default UI enabler on
    sensitivity_settings["fov"] = int(sensitivity_settings.get("fov", 210))
    sensitivity_settings["confidence"] = float(sensitivity_settings.get("confidence", 0.45))
    sensitivity_settings["activator"] = default_activator
    sensitivity_settings["activation_mode"] = sensitivity_settings.get("activation_mode", "hold")
    sensitivity_settings["gui_hotkey"] = sensitivity_settings.get("gui_hotkey", {"type": "key", "code": 0x72, "name": "F3"})  # default F3
    sensitivity_settings["aim_height"] = int(sensitivity_settings.get("aim_height", 6))
    sensitivity_settings["mouse_method"] = sensitivity_settings.get("mouse_method", "ddxoft")

    with open('lib/config/config.json', 'w') as outfile:
        json.dump(sensitivity_settings, outfile, indent=2)
    print("[INFO] Sensitivity configuration complete")

if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

    print(colored('''

  _    _   _ _   _    _    ____     _     ___ _____ _____ 
 | |  | | | | \ | |  / \  |  _ \   | |   |_ _|_   _| ____|
 | |  | | | |  \| | / _ \ | |_) |  | |    | |  | | |  _|  
 | |__| |_| | |\  |/ ___ \|  _ <   | |___ | |  | | | |___ 
 |_____\___/|_| \_/_/   \_\_| \_\  |_____|___| |_| |_____|
                                                               v2          
(Neural Network Aimbot)''', "green"))

    path_exists = os.path.exists("lib/config/config.json")
    if not path_exists or ("setup" in sys.argv):
        if not path_exists:
            print("[!] Sensitivity configuration is not set")
        setup()
    path_exists = os.path.exists("lib/data")
    if "collect_data" in sys.argv and not path_exists:
        os.makedirs("lib/data")
    from lib.aimbot import Aimbot

    # Start keyboard listener (F2 used; on_press forwarded to capture & toggle handlers)
    k_listener = keyboard.Listener(on_release=on_release, on_press=on_press)
    k_listener.start()

    # Start mouse listener (events forwarded; GUI capture will use them)
    m_listener = mouse.Listener(on_click=on_click)
    m_listener.start()

    main()
