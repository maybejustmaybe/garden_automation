import datetime
import json
import math
import os
from pathlib import Path
import time
import logging
import socket
import zoneinfo

import dotenv

dotenv.load_dotenv()

from schedule import Schedule

logger = logging.getLogger("main")

MORNING = (6, 9)
AFTERNOON = (11, 17)
NIGHT = (19, 22)


# TODO : make this actually get the response
def water():
    logger.info("Watering...")

    conn = socket.create_connection(
        (os.environ["CONTROL_MODULE_IP"], os.environ["CONTROL_MODULE_PORT"]),
    )
    try:
        conn.sendall(
            json.dumps({"request": "water", "args": {"duration": 60}}).encode("utf-8")
        )
        conn.sendall(b"\n")
    except OSError:
        return False
    finally:
        conn.shutdown(socket.SHUT_WR)

    try:
        # TODO : don't assume response will fit
        response_raw = conn.recv(1024)
        response = json.loads(response_raw.decode("utf-8"))

        res = response["success"]
        assert isinstance(res, bool), f"Response was not bool: {res}"
        return res
    finally:
        conn.close()


def get_watering_regimen(schedule):
    regimen = list()
    for (start_hour, end_hour), duration_minutes in (
        (MORNING, schedule.morning),
        (AFTERNOON, schedule.afternoon),
        (NIGHT, schedule.night),
    ):
        if duration_minutes == 0:
            continue

        assert end_hour > start_hour
        num_min_in_range = (end_hour - start_hour) * 60
        water_period_min = math.ceil(num_min_in_range / duration_minutes)

        num_waterings = 0
        for water_time_min in range(start_hour * 60, end_hour * 60, water_period_min):
            regimen.append(water_time_min)
            num_waterings += 1

            if num_waterings == duration_minutes:
                break

    return regimen


def get_last_scheduled_watering_time(regimen, now_hour, now_min):
    for cur_time in reversed(regimen):
        cur_hour = cur_time // 60
        cur_min = cur_time % 60

        if (now_hour, now_min) >= (cur_hour, cur_min):
            return (cur_hour, cur_min)

    return (regimen[-1] // 60, regimen[-1] % 60)


def get_last_watering_time():
    if not Path(os.environ["LAST_WATERING_PATH"]).exists():
        return None

    with open(os.environ["LAST_WATERING_PATH"], "r", encoding="utf-8") as f:
        hour, minute = f.readline().split(",")
        return (int(hour), int(minute))


def update_last_watering_time(t):
    with open(os.environ["LAST_WATERING_PATH"], "w", encoding="utf-8") as f:
        f.write(f"{t[0]},{t[1]}\n")


def main():
    POLL_FREQ_S = 1

    logging.basicConfig(level=logging.DEBUG)

    cur_schedule = None
    cur_regimen = None

    while True:
        try:
            new_schedule = Schedule.load()
        except json.JSONDecodeError:
            continue

        if new_schedule != cur_schedule:
            cur_schedule = new_schedule
            cur_regimen = get_watering_regimen(cur_schedule)
            logger.info(f"Updated schedule: {cur_schedule}")

            logger.debug(
                "Updated regimen: %s",
                (
                    ", ".join(f"{t // 60}:{t % 60:02d}" for t in cur_regimen)
                    if cur_regimen
                    else "<EMPTY>"
                ),
            )

        now = datetime.datetime.now(tz=zoneinfo.ZoneInfo("America/New_York"))
        if len(cur_regimen) != 0:
            last_scheduled_time = get_last_scheduled_watering_time(
                cur_regimen, now.hour, now.minute
            )

            last_watered_at_time = get_last_watering_time()

            if (
                last_watered_at_time is None
                or last_scheduled_time != last_watered_at_time
            ):
                succeeded = water()
                update_last_watering_time(last_scheduled_time)
                if succeeded:
                    logger.info("Watering succeeded.")
                else:
                    logger.info("Watering failed!")

        time.sleep(POLL_FREQ_S)


if __name__ == "__main__":
    main()
