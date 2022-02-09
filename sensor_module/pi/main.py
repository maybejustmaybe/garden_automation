import enum
import json
import logging
import multiprocessing as mp
from pathlib import Path

import pydantic
import serial

from lib import pyboard

logging.basicConfig(
    level=logging.INFO,
)

FEATHER_PORT = "/dev/ttyUSB0"
FEATHER_BAUD_RATE = 115200
ATLAS_COLOR_PORT = "/dev/ttyUSB1"
ATLAS_COLOR_BAUD_RATE = 9600

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


class SensorType(enum.Enum):
    SHT30 = "sht30"
    AHTX0 = "ahtx0"
    ATLAS_COLOR = "atlas_color"

class ReadingType(enum.Enum):
    TEMPERATURE = "temp"
    HUMIDITY = "humidity"

class SensorReadingBase(pydantic.BaseModel):
    sensor: SensorType
    tick_diff: int
    reading_type: ReadingType
    reading: float


def read_feather_sensors(queue):
    FEATHER_ENTER_REPL_NUM_RETRIES = 3

    _output_chunks = list()

    def on_feather_data(readings_json):
        readings_list = json.loads(readings_json)
        for reading_dict in readings_list:
            queue.put(SensorReadingBase(**reading_dict))

    def on_feather_output(raw):
        chunk = raw.decode("utf-8", errors="replace")
        split_chunks = chunk.split("\n")

        if len(split_chunks) > 1:
            # TODO : send data back on queue
            on_feather_data("".join([*_output_chunks, split_chunks[0]]))
            for line_chunk in split_chunks[1:-1]:
                on_feather_data(line_chunk)

            _output_chunks.clear()
            _output_chunks.append(split_chunks[-1])
        else:
            _output_chunks.append(split_chunks[0])

    logging.info("Initializing feather...")
    feather_pyboard = pyboard.Pyboard(FEATHER_PORT, FEATHER_BAUD_RATE)
    try:
        for enter_repl_attempt in range(1, FEATHER_ENTER_REPL_NUM_RETRIES + 1):
            try:
                feather_pyboard.enter_raw_repl()
            except pyboard.PyboardError:
                pass
            else:
                if enter_repl_attempt != 1:
                    logging.warn(
                        f"Entering repl on feather took {enter_repl_attempt} attempts"
                    )
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
        try:
            feather_pyboard.exec(feather_main_contents, data_consumer=on_feather_output)
        except KeyboardInterrupt:
            return
    finally:
        logging.info("Cleaning up feather.")
        feather_pyboard.exit_raw_repl()
        feather_pyboard.close()

def read_atlas_color_sensor(queue):
    atlas_color_serial = serial.Serial(ATLAS_COLOR_PORT, ATLAS_COLOR_BAUD_RATE, timeout=1)

    atlas_color_serial.write("C,1\r".encode("utf-8"))

    # TODO :remove
    import time
    while True:
        res = atlas_color_serial.read_until(b'\r')

        if res == b'':
            continue
        
        print(res)
        time.sleep(.1)

    while True:
        lsl = len(b'\r')
        line_buffer = []
        while True:
            next_char = atlas_color_serial.read(1)
            if next_char == b'':
                break
            line_buffer.append(next_char)
            if (len(line_buffer) >= lsl and
                    line_buffer[-lsl:] == [b'\r']):
                break

        res = (b''.join(line_buffer)).decode("utf-8")

        if res:
            print(res)


def main():
    mp.set_start_method("forkserver")

    sensor_reading_queue = mp.Queue()
    feather_proc = mp.Process(target=read_feather_sensors, args=(sensor_reading_queue,))
    atlas_color_proc = mp.Process(target=read_atlas_color_sensor, args=(sensor_reading_queue,))

    # TODO 
    # procs = [feather_proc, atlas_color_proc]
    procs = [atlas_color_proc]

    try:
        logging.info("Starting sensor reading gathering processes...")

        for p in procs:
            p.start()

        logging.info("Gathering data from processes...")
        while True:
            while not sensor_reading_queue.empty():
                reading = sensor_reading_queue.get()
                print(reading.json())

            for p in procs:
                if p.is_alive():
                    break
            else:
                break
    except KeyboardInterrupt:
        pass
    finally:
        logging.info("Shutting down processes.")
        for p in procs:
            if p.pid is not None:
                p.join()

    logging.info("Exiting.")


if __name__ == "__main__":
    main()
