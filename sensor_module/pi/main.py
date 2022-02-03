from lib import pyboard
from pathlib import Path

FEATHER_DEVICE = "/dev/ttyUSB0"
FEATHER_MAIN_PATH = Path("../../feather/main.py").resolve()

def main():
    with open(FEATHER_MAIN_PATH, "r", encoding="utf-8") as f:
        feather_main_contents = f.read()

    def on_feather_output(raw):
        print(raw)

    pyb = pyboard.Pyboard(FEATHER_DEVICE, 115200)
    try:
        pyb.enter_raw_repl()
        pyb.exec(feather_main_contents, data_consumer=on_feather_output)
    finally:
        pyb.exit_raw_repl()
        pyb.close()

    print("done")

if __name__ == "__main__":
    main()