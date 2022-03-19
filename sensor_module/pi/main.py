import enum
import json
import logging
import multiprocessing as mp
from multiprocessing.sharedctypes import Value
import time
from pathlib import Path
from logging.handlers import QueueHandler, QueueListener

import pydantic
import redis
import serial
import requests

from lib import pyboard


logging.basicConfig(
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_log_queue = mp.Queue()
_log_queue_handler = QueueHandler(_log_queue)

logger.addHandler(_log_queue_handler)

log_listener = QueueListener(_log_queue, logging.StreamHandler())

# TODO
# FEATHER_PORT = "/dev/ttyUSB0"
# FEATHER_BAUD_RATE = 115200
ATLAS_COLOR_PORT = "/dev/ttyUSB0"
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
    WEATHER_FORECAST_1_HOUR = "weather_forecast_1_hour"
    WEATHER_FORECAST_3_HOUR = "weather_forecast_3_hour"
    WEATHER_FORECAST_12_HOUR = "weather_forecast_12_hour"
    WEATHER_FORECAST_24_HOUR = "weather_forecast_24_hour"
    WEATHER_FORECAST_48_HOUR = "weather_forecast_48_hour"
    WEATHER_HISTORICAL = "weather_historical"


class ReadingType(enum.Enum):
    TEMPERATURE = "temp"
    HUMIDITY = "humidity"
    LUX = "lux"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WEATHER_TEMP = "weather_temp"
    WEATHER_HUMIDITY = "weather_humidity"
    WEATHER_CLOUDS = "weather_clouds"
    WEATHER_WIND_SPEED = "weather_wind_speed"

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
    value: float


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
            # TODO :  decide if this should be an error
            # raise RuntimeError(
            #     "Unexpected number of readings from atlas color sensor: '{}'".format(
            #         ",".join(reading_tuple)
            #     )
            # )
            logging.error(
                "Unexpected number of readings from atlas color sensor, skipping (reading): '{}'".format(
                    ",".join(reading_tuple)
                )
            )
            return

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
                    value=value,
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
                while (raw := atlas_color_serial.read_until(b"\r")) != b"":
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
                if e.args[0] == "TSDB: key already exists":
                    continue
                
                raise

    try:
        logging.info("Publishing sensor readings...")

        # TODO : consider optimizing this with a pipeline
        while True:
            reading = sensor_reading_queue.get(block=True)

            redis_client.ts().add(f"sensor_readings.{reading.sensor_type.value}.{reading.reading_type.value}", "*", reading.value)
            
    except KeyboardInterrupt:
        return
    finally:
        logging.info("Cleaning up redis client...")
        redis_client.close()


def get_weather(queue, data_type):
    # TODO
    # API_CALL_FREQUENCY_S = 60 * 60
    API_CALL_FREQUENCY_S = 10 
    READING_KEYS = [
        "temp",
        "humidity",
        "clouds",
        "wind_speed",
    ]

    if data_type not in ("forecast", "historical"):
        raise ValueError(f"Invalid data type: {data_type}")

    with open("./configs/weather_api.json", "r", encoding="utf-8") as f:
        WEATHER_CONFIG = json.loads(f.read())

    base_params = dict(
        lat=WEATHER_CONFIG["latitude"],
        lon=WEATHER_CONFIG["longitude"],
        units="metric",
        appid=WEATHER_CONFIG["api_key"],
    )

    try:
        while True:
            try:
                cur_time = int(time.time())

                if data_type == "forecast":
                    res = requests.get(
                        "https://api.openweathermap.org/data/2.5/onecall",
                        params=dict(
                            exclude=["current", "minutely", "daily", "alerts"],
                            **base_params,
                        ),
                    )
                elif data_type == "historical":
                    res = requests.get(
                        "https://api.openweathermap.org/data/2.5/onecall/timemachine",
                        params=dict(
                            type="hour",
                            dt=cur_time - API_CALL_FREQUENCY_S,
                            **base_params,
                        ),
                    )
                else:
                    assert False

                res.raise_for_status()

                weather_data = res.json()

                if data_type == "forecast":
                    assert len(weather_data["hourly"]) == 48
                    for forecast_type, hourly_data in (
                        (SensorType.WEATHER_FORECAST_1_HOUR, weather_data["hourly"][0]),
                        (SensorType.WEATHER_FORECAST_3_HOUR, weather_data["hourly"][2]),
                        (
                            SensorType.WEATHER_FORECAST_12_HOUR,
                            weather_data["hourly"][11],
                        ),
                        (
                            SensorType.WEATHER_FORECAST_24_HOUR,
                            weather_data["hourly"][23],
                        ),
                        (
                            SensorType.WEATHER_FORECAST_48_HOUR,
                            weather_data["hourly"][47],
                        ),
                    ):
                        for key in READING_KEYS:
                            queue.put(
                                SensorReading(
                                    sensor_type=forecast_type,
                                    reading_type=ReadingType(f"weather_{key}"),
                                    value=hourly_data[key],
                                )
                            )
                elif data_type == "historical":
                    last_hour_data = weather_data["hourly"][-1]
                    for key in READING_KEYS:
                        queue.put(
                            SensorReading(
                                sensor_type=SensorType.WEATHER_HISTORICAL,
                                reading_type=ReadingType(f"weather_{key}"),
                                value=last_hour_data[key],
                            )
                        )
                else:
                    assert False
            except Exception as e:
                logging.info(
                    f"Encountered an exception getting weather '{data_type}': {repr(e)}"
                )
            finally:
                time.sleep(API_CALL_FREQUENCY_S)
    except KeyboardInterrupt:
        return


def main():
    SENSOR_PROC_POLL_PERIOD_S = .5

    spawn_ctx = mp.get_context("forkserver")

    sensor_reading_queue = spawn_ctx.Queue()
    publish_proc = spawn_ctx.Process(target=publish_sensor_readings, args=(sensor_reading_queue,))
    feather_proc = spawn_ctx.Process(target=read_feather_sensors, args=(sensor_reading_queue,))
    atlas_color_proc = spawn_ctx.Process(
        target=read_atlas_color_sensor, args=(sensor_reading_queue,)
    )
    weather_historical_proc = spawn_ctx.Process(
        target=get_weather, args=(sensor_reading_queue, "historical")
    )
    weather_forecast_proc = spawn_ctx.Process(
        target=get_weather, args=(sensor_reading_queue, "forecast")
    )

    # TODO : pass
    # sensor_procs = [feather_proc, atlas_color_proc, weather_historical_proc, weather_forecast_proc]
    sensor_procs = [atlas_color_proc, weather_historical_proc, weather_forecast_proc]

    try:
        logging.info("Starting sensor reading gathering processes...")

        for p in sensor_procs:
            p.start()

        try:
            publish_proc.start()

            logging.info("Gathering data from processes...")

            while True:
                procs_alive = True
                for p in sensor_procs:
                    if not p.is_alive():
                        procs_alive = False
                        break
                
                if not procs_alive:
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
    try:
        log_listener.start()
        main()
    finally:
        log_listener.stop()
