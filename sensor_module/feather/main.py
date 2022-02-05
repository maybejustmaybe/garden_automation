import sys
import micropython

micropython.alloc_emergency_exception_buf(100)

import array
import json
import os
import struct
import time

from machine import I2C, Pin, Timer, UART

import ahtx0
import sht30

SHT30_I2C_ADDRESS = 0x44

DEFAULT_SENSOR_BUFFER_SIZE = 256


class BoundsException(Exception):
    pass


class SensorCallbackBase:
    BUFFER_TYPECODE = None
    VALUE_TYPECODE = None
    VALUE_TRANSFORM = lambda v: v

    def __init__(self, period, buffer_size=DEFAULT_SENSOR_BUFFER_SIZE):
        self.cb_ref = self.cb
        self.cb_wrapper_ref = self.cb_wrapper

        if struct.calcsize(self.BUFFER_TYPECODE) != struct.calcsize(
            self.VALUE_TYPECODE
        ):
            raise ValueError("Buffer and value typecode struct sizes do not match")

        self.buffer_size = buffer_size

        self.buffer = array.array(
            self.BUFFER_TYPECODE[1:],
            (0 for _ in range(buffer_size)),
        )

        self.buffer_idx_to_tick = array.array("I", (0 for _ in range(buffer_size)))
        self.buffer_idx = 0

        self.timer = Timer(-1)
        self.timer.init(period=period, callback=self.cb_wrapper)

    def clear(self):
        self.buffer_idx = 0

    def read(self):
        return [
            (
                self.buffer_idx_to_tick[idx],
                self.transform_value(
                    struct.unpack(
                        self.VALUE_TYPECODE,
                        struct.pack(
                            self.BUFFER_TYPECODE,
                            self.buffer[idx],
                        ),
                    )
                ),
            )
            for idx in range(self.buffer_idx)
        ]

    def cb_wrapper(self, timer):
        if self.buffer_idx == self.buffer_size:
            raise BoundsException("Reached sensor buffer boundary")

        # TODO : ensure sensor callback didn't take more than max time
        read_start = time.ticks_ms()
        sensor_value_buf = self.cb_ref()

        self.buffer[self.buffer_idx] = struct.unpack(
            self.BUFFER_TYPECODE, sensor_value_buf
        )[0]
        self.buffer_idx_to_tick[self.buffer_idx] = read_start
        self.buffer_idx += 1

    def cb(self, buf=None):
        raise RuntimeError("Callback method must be implemented in subclass")

    @classmethod
    def transform_value(cls, value):
        return value


class Sht30SensorCallback(SensorCallbackBase):
    BUFFER_TYPECODE = "<q"
    VALUE_TYPECODE = "<ll"

    def __init__(self, i2c, period, buffer_size=DEFAULT_SENSOR_BUFFER_SIZE):
        self.sht30_sensor = sht30.SHT30(i2c, i2c_address=SHT30_I2C_ADDRESS)

        super().__init__(period, buffer_size=buffer_size)

    def cb(self, buf=bytearray(struct.calcsize(VALUE_TYPECODE))):
        temp_int, temp_dec, humidity_int, humidity_dec = self.sht30_sensor.measure_int()

        struct.pack_into(
            self.VALUE_TYPECODE,
            buf,
            0,
            temp_int * 100 + temp_dec,
            humidity_int * 100 + humidity_dec,
        )

        return buf

    @classmethod
    def transform_value(cls, value):
        t, h = value
        return (float(t) / 100, float(h) / 100)


class Ahtx0SensorCallback(SensorCallbackBase):
    BUFFER_TYPECODE = "<q"
    VALUE_TYPECODE = "<ff"

    def __init__(self, i2c, period, buffer_size=DEFAULT_SENSOR_BUFFER_SIZE):
        self.athx0_sensor = ahtx0.AHT10(i2c)

        super().__init__(period, buffer_size=buffer_size)

    def cb(self, buf=bytearray(struct.calcsize(VALUE_TYPECODE))):
        temp = self.athx0_sensor.temperature
        humidity = self.athx0_sensor.relative_humidity

        struct.pack_into(self.VALUE_TYPECODE, buf, 0, temp, humidity)

        return buf


def main():
    SHT30_TIMER_PERIOD_MS = 300
    AHTX0_TIMER_PERIOD_MS = 500
    SENSOR_POLL_PERIOD_MS = 100
    TRANSMIT_PERIOD_MS = 1000

    UART_BAUD_RATE = 115200

    i2c = I2C(scl=Pin(5), sda=Pin(4))

    transmit_buffer = list()

    def transmit_data(_arg):
        if len(transmit_buffer) == 0:
            return

        sys.stdout.write(json.dumps(transmit_buffer))
        sys.stdout.write("\n")
        transmit_buffer.clear()

    def transmit_cb(_timer):
        micropython.schedule(transmit_data, None)

    transmit_timer = Timer(-1)
    transmit_timer.init(period=TRANSMIT_PERIOD_MS, callback=transmit_cb)

    def sht30_on_read(tick, last_tick, temp, humidity):
        transmit_buffer.append(
            {
                "type": "reading",
                "sensor": "sht30",
                "tick_diff": tick - last_tick,
                "temp": temp,
                "humidity": humidity
            }
        )

    def ahtx0_on_read(tick, last_tick, temp, humidity):
        transmit_buffer.append(
            {
                "type": "reading",
                "sensor": "ahtx0",
                "tick_diff": tick - last_tick,
                "temp": temp,
                "humidity": humidity
            }
        )

    sensor_name_to_cb = {
        "sht30": Sht30SensorCallback(i2c, SHT30_TIMER_PERIOD_MS),
        "ahtx0": Ahtx0SensorCallback(i2c, AHTX0_TIMER_PERIOD_MS),
    }
    sensor_name_to_on_read = {
        "sht30": sht30_on_read,
        "ahtx0": ahtx0_on_read,
    }
    sensor_name_to_last_tick = {
        "sht30": time.ticks_ms(),
        "ahtx0": time.ticks_ms(),
    }

    def read_sensor(sensor_name):
        sensor_cb = sensor_name_to_cb[sensor_name]

        try:
            irq_state = machine.disable_irq()
            if sensor_cb.buffer_idx == 0:
                return

            for tick, reading in sensor_cb.read():
                sensor_name_to_on_read[sensor_name](
                    tick,
                    sensor_name_to_last_tick[sensor_name],
                    *reading,
                )
                sensor_name_to_last_tick[sensor_name] = tick

            sensor_cb.clear()
        finally:
            machine.enable_irq(irq_state)

    while True:
        poll_loop_start_time_ms = time.ticks_ms()

        try:
            for sensor_name in sensor_name_to_cb.keys():
                read_sensor(sensor_name)
        finally:
            poll_loop_duration_time_ms = time.ticks_ms() - poll_loop_start_time_ms
            time.sleep_ms(SENSOR_POLL_PERIOD_MS - poll_loop_duration_time_ms)


if __name__ == "__main__":
    main()
