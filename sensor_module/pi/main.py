import serial
import json

def main():
    port = serial.Serial("/dev/ttyUSB0", baudrate=9600)
    while True:
        readings = port.readline().decode("utf-8")
        print(readings)

if __name__ == "__main__":
    main()