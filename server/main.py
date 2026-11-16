"""arisrvx's entry point for the sublevels service:

    arisrvx --python server/main.py --doc server/share/sublevels/server.ari [relay flags]

arisrvx runs a script by path (``--python`` takes no ``-m``) and passes it no arguments, so this
file finds the package beside it (server/src) and runs ``sublevels.server.main``. The HTTP
address comes from ``$SUBLEVELS_HTTP_LISTEN`` (see sublevels/server.py).
"""

import os
import sys

_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if os.path.isdir(_src) and _src not in sys.path:
    sys.path.insert(0, _src)

from sublevels.server import main  # noqa: E402

# A non-zero status stops the program with it. On success the program just ends: arisrvx then
# reports its status, and `arisrvx check` writes its report (a SystemExit would skip the report).
status = main([])
if status:
    raise SystemExit(status)
