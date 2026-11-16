"""Run the NMDC hub alone, on plain asyncio with the standard library only (no pycvc, no arisrvx,
no HTTP): for interop runs and the soak, where the hub is what is measured. Settings are the
``SUBLEVELS_*`` variables (``HubConfig.from_env``). It writes ``hub.stats()`` as JSON to
``--stats FILE`` every second and on exit, and stops on SIGTERM or SIGINT.

    python tools/run_hub.py [--stats FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from sublevels.nmdc import Hub, HubConfig, start_listeners  # noqa: E402


async def main(stats_path: str) -> int:
    hub = Hub(HubConfig.from_env())
    listeners = await start_listeners(hub)
    for proto, host, port in listeners.addresses():
        print(f"listening {proto} {host} {port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def dump():
        if stats_path:
            tmp = stats_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(hub.stats(), f)
            os.replace(tmp, stats_path)

    while not stop.is_set():
        dump()
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except TimeoutError:
            pass
    await listeners.close(hub)
    dump()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stats", default="")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="[hub] %(name)s: %(levelname)s: %(message)s")
    sys.exit(asyncio.run(main(a.stats)))
