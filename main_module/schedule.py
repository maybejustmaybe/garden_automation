import json
import os
from typing import ClassVar
from pathlib import Path

import attr


@attr.s(auto_attribs=True)
class Schedule:
    SCHEDULE_PATH: ClassVar[Path] = Path(os.environ["WATERING_SCHEDULE_PATH"])

    morning: int
    afternoon: int
    night: int

    @classmethod
    def load(cls):
        with open(cls.SCHEDULE_PATH, "r", encoding="utf-8") as f:
            return cls(**json.load(f))

    def dump(self):
        with open(self.SCHEDULE_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(attr.asdict(self)))
