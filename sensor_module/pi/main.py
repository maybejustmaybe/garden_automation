from lib import pyboard

FEATHER_DEVICE = "/dev/ttyUSB0"

def main():
    feather = pyboard.Pyboard(FEATHER_DEVICE)
    try:
        print("Testing...")
        feather.exec("print('test')")
        print("Reading...")
        feather.read_until(1, "\n", data_consumer=print)
    finally:
        feather.close()

    print("done")

if __name__ == "__main__":
    main()