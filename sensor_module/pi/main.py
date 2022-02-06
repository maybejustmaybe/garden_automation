import logging
from lib import pyboard
from pathlib import Path
import multiprocessing as mp

FEATHER_DEVICE = "/dev/ttyUSB0"
FEATHER_BAUD_RATE = 115200

FEATHER_DIR_PATH = Path(__file__).resolve().parents[1] / "feather"
FEATHER_MAIN_PATH = FEATHER_DIR_PATH / "main.py"
FEATHER_LIB_DIR_PATH = FEATHER_DIR_PATH / "lib"

# TODO : remove
# import sys
# import importlib
# import importlib.util
# def load_esptool_module():
#     ESPTOOL_PATH = Path(sys.exec_prefix) / "bin" / "esptool.py"
# 
#     esptool_spec = importlib.util.spec_from_file_location("esptool", str(ESPTOOL_PATH))
#     esptool = importlib.util.module_from_spec(esptool_spec)
#     esptool_spec.loader.exec_module(esptool)
# 
#     return esptool
# 
# esptool = load_esptool_module()
#
# def reset_feather():
#     print("Resetting feather...")
#     esp_loader = esptool.ESPLoader.detect_chip(port=FEATHER_DEVICE)    
#     esp_loader.soft_reset(True)
#     print("Reset feather.")

def read_feather_sensors(queue):
    FEATHER_ENTER_REPL_NUM_RETRIES = 3

    _output_chunks = list()
    def on_feather_output(raw):
        chunk = raw.decode("utf-8", errors="replace")
        split_chunks = chunk.split("\n")

        if len(split_chunks) > 1:
            # TODO : send data back on queue
            print("".join([*_output_chunks, split_chunks[0]]))
            for line_chunk in split_chunks[1:-1]:
                print(line_chunk)

            _output_chunks.clear()
            _output_chunks.append(split_chunks[-1])
        else:
            _output_chunks.append(split_chunks[0])

    logging.info("Initializing feather...")
    feather_pyboard = pyboard.Pyboard(FEATHER_DEVICE, FEATHER_BAUD_RATE)
    try:
        for enter_repl_attempt in range(1, FEATHER_ENTER_REPL_NUM_RETRIES + 1):
            try:
                feather_pyboard.enter_raw_repl()
            except pyboard.PyboardError:
                pass
            else:
                if enter_repl_attempt != 1:
                    logging.warn(f"Entering repl on feather took {enter_repl_attempt} attempts")
                break
        else:
            raise RuntimeError("Feather failed to enter repl")

        logging.info("Removing and reputting feather libs...")
        feather_pyboard.exec(
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
            feather_pyboard.fs_put(lib_path, f"/lib/{lib_path.name}")

        with open(FEATHER_MAIN_PATH, "r", encoding="utf-8") as f:
            feather_main_contents = f.read()

        logging.info("Feather initialization complete.")

        logging.info("Exec-ing feather main program...")
        feather_pyboard.exec(feather_main_contents, data_consumer=on_feather_output)
    finally:
        logging.info("Cleaning up feather.")
        feather_pyboard.exit_raw_repl()
        feather_pyboard.close()

    print("done")

def main():
    logging.info("Starting sensor reading gathering processes...")
    mp.set_start_method("forkserver")
    sensor_reading_queue = mp.Queue()
    feather_proc = mp.Process(target=read_feather_sensors, args=(sensor_reading_queue,))

    try:
        feather_proc.start()
    finally:
        feather_proc.join()

    logging.info("Exiting.")

if __name__ == "__main__":
    main()