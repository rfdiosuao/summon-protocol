"""Fresh COM9 reads for the reBot Arm 102 leader on the Windows demo host."""
from __future__ import annotations

import time


class ReopeningLeader:
    """Open per frame because persistent sync_monitor returns cached axes here."""

    def __init__(self, port: str = "COM9") -> None:
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def ping(self, axis: int) -> bool:
        from motorbridge_smart_servo import FashionStarServo

        with FashionStarServo(self.port, baudrate=1_000_000) as bus:
            return bool(bus.ping(axis))

    def sync_monitor(self, axes: list[int]):
        from motorbridge_smart_servo import FashionStarServo

        with FashionStarServo(self.port, baudrate=1_000_000) as bus:
            return bus.sync_monitor(axes)

    def unlock_all(self) -> None:
        """Unload the manually guided leader; do not change calibration."""
        from motorbridge_smart_servo import FashionStarServo

        with FashionStarServo(self.port, baudrate=1_000_000) as bus:
            for axis in range(7):
                bus.unlock(axis)
                time.sleep(0.02)
