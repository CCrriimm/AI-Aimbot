import json
import os
import sys
from pynput import keyboard
from pynput import mouse
from termcolor import colored

def on_release(key):
    try:
        # F1 removed (replaced by mouse side button)
        if key == keyboard.Key.f2:
            Aimbot.clean_up()
    except NameError:
        pass

def on_click(x, y, button, pressed):
    # Use the release event for a toggle (pressed==False -> release)
    if pressed:
        return
    try:
        # Toggle aimbot on side button X1 release (Button.x1)
        if button == mouse.Button.x1:
            Aimbot.update_status_aimbot()
        # Optional: use X2 to quit (uncomment if desired)
        # elif button == mouse.Button.x2:
        #     Aimbot.clean_up()
    except NameError:
        pass

def main():
    global lunar
    lunar = Aimbot(collect_data = "collect_data" in sys.argv)
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

    with open('lib/config/config.json', 'w') as outfile:
        json.dump(sensitivity_settings, outfile)
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

    # Start keyboard listener (only F2 used now)
    listener = keyboard.Listener(on_release=on_release)
    listener.start()

    # Start mouse listener (Button.x1 toggles aimbot on release)
    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()

    main()
