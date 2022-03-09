import micropython
micropython.alloc_emergency_exception_buf(100)

import time
import json
import socket

import network
from machine import Pin


PORT = 8081

def connect_to_wifi():
    print("Connecting to network...")

    with open("/configs/wifi.json", "r", encoding="utf-8") as f:
        wifi_config = json.loads(f.read())

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(wifi_config["name"], wifi_config["password"])
    while not wlan.isconnected():
        time.sleep_ms(1)

    print("Connected (IP): {}".format(wlan.ifconfig()[0]))


def water(duration):
    VALVE_PIN_NO = 5
    PUMP_PIN_NO = 4
    FLOAT_SWITCH_LOW_PIN_NO = 14

    FLOAT_SWITCH_CHECK_PERIOD_MS = 300
    PUMP_BUFFER_SLEEP_MS = 10 * 1000

    print("Watering for: {}s".format(duration))

    valve_pin = Pin(VALVE_PIN_NO, Pin.OUT)
    pump_pin = Pin(PUMP_PIN_NO, Pin.OUT)
    float_switch_low_pin = Pin(FLOAT_SWITCH_LOW_PIN_NO, Pin.IN, Pin.PULL_UP)

    class FloatSwitchLowException(Exception):
        @classmethod
        def check(cls):
            if float_switch_low_pin.value() == 1:
                raise cls()

    def sleep_until_timeout_or_float_switch_low(duration_ms):
        _start = time.ticks_ms()
        while True:
            _elapsed_time = time.ticks_ms() - _start
            if _elapsed_time >= duration_ms:
                break

            FloatSwitchLowException.check()
            time.sleep_ms(min(FLOAT_SWITCH_CHECK_PERIOD_MS, duration_ms - _elapsed_time))

    valve_opened = False
    pump_engaged = False
    try:
        FloatSwitchLowException.check()

        print("Opening valve...")
        valve_opened = True
        valve_pin.on()

        sleep_until_timeout_or_float_switch_low(PUMP_BUFFER_SLEEP_MS)

        try:
            print("Turning on pump...")
            pump_engaged = True
            pump_pin.on()

            sleep_until_timeout_or_float_switch_low(duration * 1000)
        finally:
            print("Turning off pump.")
            pump_pin.off()
    except FloatSwitchLowException:
        print("Float switch low triggered, exiting.")
        return False
    finally:
        if valve_opened:
            if pump_engaged:
                time.sleep_ms(PUMP_BUFFER_SLEEP_MS)
            print("Closing valve.")
            valve_pin.off()

    return True

def main():
    REQUEST_TYPE_TO_ARGS = {
        "water": ["duration"],
    }
    REQUEST_TYPE_TO_FUNC = {
        "water": water
    }

    connect_to_wifi()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("0.0.0.0", PORT))
    server_socket.listen(1)

    def read_from_socket(client_socket):
        BUFSIZE = 2048
        chunks = []
        while True:
            cur_chunk = client_socket.recv(BUFSIZE)
            if cur_chunk == b"":
                break
            chunks.append(cur_chunk)
        
        return b"".join(chunks).decode("utf-8")

    print("Listening for connections...")

    while True:
        client_socket = server_socket.accept()[0]

        try:
            print("Reading payload from client...")
            payload_raw = read_from_socket(client_socket)

            if payload_raw[-1] != "\n":
                print("Received invalid payload: not terminated with a newline")
                client_socket.send(json.dumps({"success": False}))
                continue

            try:
                payload_dict = json.loads(payload_raw)
            except ValueError:
                print("Received invalid payload: bad json")
                client_socket.send(json.dumps({"success": False}))
                continue

            request = payload_dict.get("request")
            if payload_dict.get("request") not in REQUEST_TYPE_TO_ARGS.keys():
                print("Received invalid payload: unknown request")
                client_socket.send(json.dumps({"success": False}))
                continue

            request_args = payload_dict.get("args")
            if request_args is None:
                print("Received invalid payload: missing args")
                client_socket.send(json.dumps({"success": False}))
                continue

            try:
                res = REQUEST_TYPE_TO_FUNC[request](**request_args)
            except Exception as e:
                print("Encountered an exception when processing request: {}".format(e))
                client_socket.send(json.dumps({"success": False}))
            else:
                client_socket.send(json.dumps({"success": res}))
        except Exception:
            try:
                client_socket.send(json.dumps({"success": False}))
            except Exception:
                pass

            raise
        finally:
            client_socket.close()

if __name__ == "__main__":
    main()