"""sublevels: a Direct Connect hub for the Ariadne stack, run as an arisrvx service.

The service layer is :mod:`sublevels.service`. Its front ends are the HTTP API
(:mod:`sublevels.api`), the room-call exports (:mod:`sublevels.rpc`) and, from M1, the NMDC
listener (:mod:`sublevels.nmdc`). :mod:`sublevels.server` runs them in one process.
"""

__version__ = "0.1.0"

# The published namespace: the state subtree the service writes and native clients mirror. The
# server document (server/share/sublevels/server.ari) publishes the same name.
NAMESPACE = "sublevels"
