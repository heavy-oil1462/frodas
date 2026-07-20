#!/usr/bin/env python3
"""frodas sim container manager - a simulated greenhouse against YOUR stack.

Thin entry point for esphome_skills.simc with the frodas project; see
`--help` and docs/SIMULATION.md.

    python3 tools/sim_container.py build
    python3 tools/sim_container.py run --broker 192.168.1.10 \
        --username frodas --password secret
    python3 tools/sim_container.py logs|status|stop
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills import simc  # noqa: E402

if __name__ == "__main__":
    sys.exit(simc.main(PROJECT))
