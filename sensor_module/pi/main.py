from lib import pyboard, PyboardError
from pathlib import Path

FEATHER_DEVICE = "/dev/ttyUSB0"

FEATHER_DIR_PATH = Path(__file__).resolve().parents[1] / "feather"
FEATHER_MAIN_PATH = FEATHER_DIR_PATH / "main.py"
FEATHER_LIB_DIR_PATH = FEATHER_DIR_PATH / "lib"

def main():
    with open(FEATHER_MAIN_PATH, "r", encoding="utf-8") as f:
        feather_main_contents = f.read()

    def on_feather_output(raw):
        print(raw)

    pyb = pyboard.Pyboard(FEATHER_DEVICE, 115200)
    try:
        pyb.enter_raw_repl()
        print("Removing and reputting feather libs...")
        try:
            pyb.fs_rmdir("/lib")
        except PyboardError:
            # NOTE that the lib dir was likely not present
            pass

        pyb.fs_mkdir("/lib")
        for lib_path in FEATHER_LIB_DIR_PATH.iterdir():
            assert lib_path.is_file()
            pyb.fs_put(lib_path, f"/lib/{lib_path.name}")

        pyb.exec(feather_main_contents, data_consumer=on_feather_output)
    finally:
        pyb.exit_raw_repl()
        pyb.close()

    print("done")

if __name__ == "__main__":
    main()