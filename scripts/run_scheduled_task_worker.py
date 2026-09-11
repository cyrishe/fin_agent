from __future__ import annotations

import argparse
import logging
import signal
import threading

from src.services.scheduled_task_worker import ScheduledTaskWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the durable Fin Agent schedule worker.")
    parser.add_argument("--once", action="store_true", help="materialize/claim at most one run")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=3600)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = ScheduledTaskWorker(lease_seconds=args.lease_seconds)
    stopping = threading.Event()

    def stop_worker(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    while not stopping.is_set():
        outcome = worker.run_once()
        logging.info("scheduled worker outcome=%s", outcome)
        if args.once:
            return
        stopping.wait(max(0.5, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
