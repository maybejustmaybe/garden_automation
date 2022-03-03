import micropython
micropython.alloc_emergency_exception_buf(100)

import time
import json
import socket

import network
from machine import Pin


VALVE_PIN_NO = 5
PUMP_PIN_NO = 4

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
    PUMP_BUFFER_SLEEP_MS = 10 * 1000

    print("Watering for: {}s".format(duration))

    valve_pin = Pin(VALVE_PIN_NO, Pin.OUT)
    pump_pin = Pin(PUMP_PIN_NO, Pin.OUT)

    try:
        print("Opening valve...")
        valve_pin.on()
        time.sleep_ms(PUMP_BUFFER_SLEEP_MS)

        try:
            print("Turning on pump...")
            pump_pin.on()

            time.sleep_ms(duration * 1000)
        finally:
            print("Turning off pump.")
            pump_pin.off()
        
    finally:
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