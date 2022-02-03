from lib import pyboard

FEATHER_DEVICE = "/dev/ttyUSB0"

def main():
    pyb = pyboard.Pyboard(FEATHER_DEVICE, 115200)
    try:
        print("REPL...")
        pyb.enter_raw_repl()
        print("Testing...")
        ret = pyb.exec('print(1+1)')
        print(ret)
    finally:
        pyb.exit_raw_repl()
        pyb.close()

    print("done")

if __name__ == "__main__":
    main()