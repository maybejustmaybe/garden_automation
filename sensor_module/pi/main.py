import sys
import importlib
import importlib.util
from lib import pyboard
from pathlib import Path

FEATHER_DEVICE = "/dev/ttyUSB0"

FEATHER_DIR_PATH = Path(__file__).resolve().parents[1] / "feather"
FEATHER_MAIN_PATH = FEATHER_DIR_PATH / "main.py"
FEATHER_LIB_DIR_PATH = FEATHER_DIR_PATH / "lib"

def load_esptool_module():
    ESPTOOL_PATH = Path(sys.exec_prefix) / "bin" / "esptool.py"

    esptool_spec = importlib.util.spec_from_file_location("esptool", str(ESPTOOL_PATH))
    esptool = importlib.util.module_from_spec(esptool_spec)
    esptool_spec.loader.exec_module(esptool)

    return esptool

esptool = load_esptool_module()


def reset_feather():
    print("Resetting feather...")
    esp_loader = esptool.ESPLoader.detect_chip(port=FEATHER_DEVICE)    
    esp_loader.hard_reset()
    print("Reset feather.")

def main():
    with open(FEATHER_MAIN_PATH, "r", encoding="utf-8") as f:
        feather_main_contents = f.read()

    output_chunks = list()
    def on_feather_output(raw):
        chunk = raw.decode("utf-8", errors="replace")
        split_chunks = chunk.split("\n")

        if len(split_chunks) > 1:
            print("".join([*output_chunks, split_chunks[0]]))
            for line_chunk in split_chunks[1:-1]:
                print(line_chunk)
            output_chunks.clear()
            output_chunks.append(split_chunks[-1])
        else:
            output_chunks.append(split_chunks[0])

    # TODO
    # reset_feather()
    pyb = pyboard.Pyboard(FEATHER_DEVICE, 115200)
    try:
        pyb.enter_raw_repl()

        print("Removing and reputting feather libs...")
        pyb.exec(
"""
import os
try:
    os.stat("/lib")
except FileNotFoundError:
    os.mkdir("/lib")
else:
    for path in os.listdir("/lib"):
        os.remove("/lib/{}".format(path))
"""
        )

        for lib_path in FEATHER_LIB_DIR_PATH.iterdir():
            assert lib_path.is_file()
            pyb.fs_put(lib_path, f"/lib/{lib_path.name}")

        print("Exec-ing feather main program...")
        # TODO : remove
        # pyb.exec(feather_main_contents, data_consumer=on_feather_output)
        pyb.exec("print('fooooooo')", data_consumer=on_feather_output)
    finally:
        pyb.exit_raw_repl()
        pyb.close()

    print("done")

if __name__ == "__main__":
    main()