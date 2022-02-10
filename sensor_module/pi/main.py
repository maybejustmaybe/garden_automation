import enum
import json
import logging
import multiprocessing as mp
import time
from pathlib import Path

import pydantic
import redis
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

REDIS_PORT = 7661
REDIS_RETENTION_MS = 30 * 60 * 1000

class SensorType(enum.Enum):
    SHT30 = "sht30"
    AHTX0 = "ahtx0"
    ATLAS_COLOR = "atlas_color"


class ReadingType(enum.Enum):
    TEMPERATURE = "temp"
    HUMIDITY = "humidity"
    LUX = "lux"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"

SENSOR_TYPE_TO_READING_TYPES = {
    SensorType.SHT30: (ReadingType.TEMPERATURE, ReadingType.HUMIDITY),
    SensorType.AHTX0: (ReadingType.TEMPERATURE, ReadingType.HUMIDITY),
    SensorType.ATLAS_COLOR: (ReadingType.LUX, ReadingType.RED, ReadingType.GREEN, ReadingType.BLUE),
}


class SensorReading(pydantic.BaseModel):
    sensor_type: SensorType
    # TODO : consider adding this back for monitoring purposes
    # tick_diff: int
    reading_type: ReadingType
    reading: float


def read_feather_sensors(queue):
    FEATHER_ENTER_REPL_NUM_RETRIES = 3

    _output_chunks = list()

    def on_feather_data(readings_json):
        readings_list = json.loads(readings_json)
        for reading_dict in readings_list:
            queue.put(SensorReading(**reading_dict))

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
        logging.info("Cleaning up feather...")
        feather_pyboard.exit_raw_repl()
        feather_pyboard.close()


def read_atlas_color_sensor(queue):
    CONTINUOUS_POLL_PERIOD_CONST_MS = 400
    CONTINUOUS_POLL_MULTIPLIER = 3
    POLL_PERIOD_MS = CONTINUOUS_POLL_PERIOD_CONST_MS * CONTINUOUS_POLL_MULTIPLIER

    SERIAL_TIMEOUT_S = POLL_PERIOD_MS * 8 / 1000
    SERIAL_READ_PERIOD_MS = POLL_PERIOD_MS * 4

    atlas_color_serial = serial.Serial(
        ATLAS_COLOR_PORT, ATLAS_COLOR_BAUD_RATE, timeout=SERIAL_TIMEOUT_S
    )

    atlas_color_serial.write("C,0\r".encode("utf-8"))
    atlas_color_serial.write("O,LUX,1\r".encode("utf-8"))
    atlas_color_serial.write("C,1\r".encode("utf-8"))
    atlas_color_serial.flush()

    def on_sensor_data(reading_tuple):
        if len(reading_tuple) != 5:
            raise RuntimeError(
                "Unexpected number of readings from atlas color sensor: '{}'".format(
                    ",".join(reading_tuple)
                )
            )

        red_value, green_value, blue_value, lux_sentinel, lux_value = reading_tuple

        assert lux_sentinel == "Lux"

        for value, reading_type in zip(
            (red_value, green_value, blue_value, lux_value),
            (ReadingType.RED, ReadingType.GREEN, ReadingType.BLUE, ReadingType.LUX),
        ):
            queue.put(
                SensorReading(
                    sensor_type=SensorType.ATLAS_COLOR,
                    reading_type=reading_type,
                    reading=value,
                )
            )

    def on_sensor_output(raw):
        if raw[-1:] != b"\r":
            raise RuntimeError(
                "Atlas color sensor output did not end with carriage return: '{}'".format(
                    raw.decode("utf-8")
                )
            )
        
        if raw == b"*OK\r" or raw == b"\x00\r":
            return

        on_sensor_data(raw[:-1].decode("utf-8").split(","))

    try:
        while True:
            loop_start = time.monotonic()

            try:
                while (raw := atlas_color_serial.read_until(b"\r")) != b'':
                    on_sensor_output(raw)
            finally:
                loop_duration_ms = time.monotonic() - loop_start
                time.sleep(max(0, (SERIAL_READ_PERIOD_MS - loop_duration_ms) / 1000))
    except KeyboardInterrupt:
        return
    finally:
        logging.info("Cleaning up atlas color sensor...")
        atlas_color_serial.close()

def publish_sensor_readings(sensor_reading_queue):
    redis_client = redis.Redis(host="localhost", port=REDIS_PORT)

    for sensor_type, reading_types in SENSOR_TYPE_TO_READING_TYPES.items():
        for r_type in reading_types:
            try:
                redis_client.ts().create(f"sensor_readings.{sensor_type.value}.{r_type.value}", retension_msecs=REDIS_RETENTION_MS)
            except redis.exceptions.ResponseError as e:
                # TODO
                import pdb; pdb.set_trace()
                raise
                

    # TODO
    return

    try:
        logging.info("Publishing sensor readings...")
        while True:
            reading = sensor_reading_queue.get(block=True)

            redis_client.publish("sensor_module.readings.{}".format(reading.sensor_type.value), reading.json())
    except KeyboardInterrupt:
        return
    finally:
        logging.info("Cleaning up redis client...")
        redis_client.close()


def main():
    # TODO
    publish_sensor_readings(mp.Queue())
    return

    SENSOR_PROC_POLL_PERIOD_S = .5

    mp.set_start_method("forkserver")

    sensor_reading_queue = mp.Queue()
    publish_proc = mp.Process(target=publish_sensor_readings, args=(sensor_reading_queue,))
    feather_proc = mp.Process(target=read_feather_sensors, args=(sensor_reading_queue,))
    atlas_color_proc = mp.Process(
        target=read_atlas_color_sensor, args=(sensor_reading_queue,)
    )

    sensor_procs = [feather_proc, atlas_color_proc]


    try:
        logging.info("Starting sensor reading gathering processes...")

        for p in sensor_procs:
            p.start()

        try:
            publish_proc.start()

            logging.info("Gathering data from processes...")

            while True:
                for p in sensor_procs:
                    if p.is_alive():
                        break
                else:
                    break

                time.sleep(SENSOR_PROC_POLL_PERIOD_S)
        finally:
            publish_proc.terminate()
            publish_proc.join()
    except KeyboardInterrupt:
        pass
    finally:
        logging.info("Shutting down processes.")
        for p in sensor_procs:
            if p.pid is not None:
                p.terminate()
                p.join()

    logging.info("Exiting.")


if __name__ == "__main__":
    main()
