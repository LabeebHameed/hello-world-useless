"""Headless simulation process; no browser, web framework, or network required."""

import argparse
import math
from pathlib import Path
import signal
import threading

from world import World


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("tick interval must be finite and positive")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", type=Path, default=Path("data/world.json"))
    parser.add_argument("--tick-seconds", type=positive_seconds, default=1.0)
    args = parser.parse_args()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    is_new = not args.save.exists()
    world = World(args.save)
    if is_new:
        world.start()
    print(f"World loaded at tick {world.snapshot()['clock']['tick']}; save: {args.save}", flush=True)
    # Event.wait uses a monotonic timeout. Downtime is never added to world time.
    while not stop.wait(args.tick_seconds):
        world.tick_if_running()
    world.save()


if __name__ == "__main__":
    main()
