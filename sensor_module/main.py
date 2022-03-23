import enum
import json
import logging
import multiprocessing as mp
import time
from logging.handlers import QueueHandler, QueueListener

import pydantic
import redis
import serial
import smbus
import requests


logging.basicConfig(
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_log_queue = mp.Queue()
_log_queue_handler = QueueHandler(_log_queue)

logger.addHandler(_log_queue_handler)

log_listener = QueueListener(_log_queue, logging.StreamHandler())

ATLAS_COLOR_PORT = "/dev/ttyUSB0"
ATLAS_COLOR_BAUD_RATE = 9600

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
    WEATHER_RAIN = "weather_rain"


_WEATHER_READING_TYPES = [
    ReadingType.WEATHER_TEMP,
    ReadingType.WEATHER_HUMIDITY,
    ReadingType.WEATHER_CLOUDS,
    ReadingType.WEATHER_WIND_SPEED,
    ReadingType.WEATHER_RAIN,
]


SENSOR_TYPE_TO_READING_TYPES = {
    SensorType.SHT30: (ReadingType.TEMPERATURE, ReadingType.HUMIDITY),
    SensorType.AHTX0: (ReadingType.TEMPERATURE, ReadingType.HUMIDITY),
    SensorType.ATLAS_COLOR: (
        ReadingType.LUX,
        ReadingType.RED,
        ReadingType.GREEN,
        ReadingType.BLUE,
    ),
    SensorType.WEATHER_FORECAST_1_HOUR: _WEATHER_READING_TYPES,
    SensorType.WEATHER_FORECAST_3_HOUR: _WEATHER_READING_TYPES,
    SensorType.WEATHER_FORECAST_12_HOUR: _WEATHER_READING_TYPES,
    SensorType.WEATHER_FORECAST_24_HOUR: _WEATHER_READING_TYPES,
    SensorType.WEATHER_FORECAST_48_HOUR: _WEATHER_READING_TYPES,
    SensorType.WEATHER_HISTORICAL: _WEATHER_READING_TYPES,
}


class SensorReading(pydantic.BaseModel):
    sensor_type: SensorType
    # TODO : consider adding this back for monitoring purposes
    # tick_diff: int
    reading_type: ReadingType
    value: float

def read_sht30_sensor(queue):
    ADDRESS = 0x44
    READ_DELAY_S = .1
    READ_FREQ_S = 3
    assert READ_FREQ_S >= READ_DELAY_S

    bus = smbus.SMBus(1)

    def _check_crc(data):
        POLYNOMIAL = 0x131  # P(x) = x^8 + x^5 + x^4 + 1 = 100110001

        # calculates 8-Bit checksum with given polynomial
        crc = 0xFF

        for b in data[:-1]:
            crc ^= b
            for _ in range(8, 0, -1):
                if crc & 0x80:
                    crc = (crc << 1) ^ POLYNOMIAL
                else:
                    crc <<= 1
        crc_to_check = data[-1]
        return crc_to_check == crc

    while True:
        # Send measurement command, 0x2C(44)
        #		0x06(06)	High repeatability measurement
        bus.write_i2c_block_data(ADDRESS, 0x2C, [0x06])

        time.sleep(READ_DELAY_S)

        data = bus.read_i2c_block_data(ADDRESS, 0x00, 6)
        
        # NOTE that position 2 and 5 are crc
        check_res = _check_crc(data[0:3]) and _check_crc(data[3:6])
        if not check_res:
            logging.error("Failed crc check for SHT30 sensor, skipping.")
            continue

        temp = (((data[0] << 8 |  data[1]) * 175) / 0xFFFF) - 45
        relative_humidity = ((data[3] << 8 | data[4]) * 100.0) / 0xFFFF

        queue.put(
            SensorReading(
                sensor_type=SensorType.SHT30,
                reading_type=ReadingType.TEMPERATURE,
                value=temp,
            )
        )
        queue.put(
            SensorReading(
                sensor_type=SensorType.SHT30,
                reading_type=ReadingType.HUMIDITY,
                value=relative_humidity,
            )
        )

        time.sleep(READ_FREQ_S - READ_DELAY_S)


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


def get_weather(queue, data_type):
    API_CALL_FREQUENCY_S = 60 * 60
    READING_KEYS = [
        "temp",
        "humidity",
        "clouds",
        "wind_speed",
        "rain",
    ]
    READING_KEY_TO_DEFAULT = {"rain": 0}

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
                            value = hourly_data.get(key)
                            if value is None:
                                default = READING_KEY_TO_DEFAULT.get(key)
                                if default is not None:
                                    value = default
                                else:
                                    raise RuntimeError(
                                        "Reading key missing from data: {key}"
                                    )

                                assert value is not None
                            queue.put(
                                SensorReading(
                                    sensor_type=forecast_type,
                                    reading_type=ReadingType(f"weather_{key}"),
                                    value=value,
                                )
                            )
                elif data_type == "historical":
                    last_hour_data = weather_data["hourly"][-1]
                    for key in READING_KEYS:
                        value = last_hour_data.get(key)
                        if value is None:
                            default = READING_KEY_TO_DEFAULT.get(key)
                            if default is not None:
                                value = default
                            else:
                                raise RuntimeError(
                                    "Reading key missing from data: {key}"
                                )

                        assert value is not None

                        queue.put(
                            SensorReading(
                                sensor_type=SensorType.WEATHER_HISTORICAL,
                                reading_type=ReadingType(f"weather_{key}"),
                                value=value,
                            )
                        )
                else:
                    assert False
            except Exception as e:
                # TODO : narrow exception case
                logging.error(
                    f"Encountered an exception getting weather '{data_type}': {repr(e)}"
                )
            finally:
                time.sleep(API_CALL_FREQUENCY_S)
    except KeyboardInterrupt:
        return


def publish_sensor_readings(sensor_reading_queue):
    redis_client = redis.Redis(host="localhost", port=REDIS_PORT)

    for sensor_type, reading_types in SENSOR_TYPE_TO_READING_TYPES.items():
        for r_type in reading_types:
            try:
                redis_client.ts().create(
                    f"sensor_readings.{sensor_type.value}.{r_type.value}",
                    retension_msecs=REDIS_RETENTION_MS,
                )
            except redis.exceptions.ResponseError as e:
                if e.args[0] == "TSDB: key already exists":
                    continue

                raise

    try:
        logging.info("Publishing sensor readings...")

        # TODO : consider optimizing this with a pipeline
        while True:
            reading = sensor_reading_queue.get(block=True)

            redis_client.ts().add(
                f"sensor_readings.{reading.sensor_type.value}.{reading.reading_type.value}",
                "*",
                reading.value,
            )

    except KeyboardInterrupt:
        return
    finally:
        logging.info("Cleaning up redis client...")
        redis_client.close()


def main():
    SENSOR_PROC_POLL_PERIOD_S = 0.5

    spawn_ctx = mp.get_context("forkserver")

    sensor_reading_queue = spawn_ctx.Queue()

    publish_proc = spawn_ctx.Process(
        target=publish_sensor_readings, args=(sensor_reading_queue,)
    )
    atlas_color_proc = spawn_ctx.Process(
        target=read_atlas_color_sensor, args=(sensor_reading_queue,)
    )
    weather_historical_proc = spawn_ctx.Process(
        target=get_weather, args=(sensor_reading_queue, "historical")
    )
    weather_forecast_proc = spawn_ctx.Process(
        target=get_weather, args=(sensor_reading_queue, "forecast")
    )

    # TODO : add sht30 proc
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
