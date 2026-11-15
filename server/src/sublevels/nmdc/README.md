# sublevels.nmdc

The hub side of NMDC goes here in M1 (docs/ROADMAP.md): an asyncio listener on TCP 411, and
NMDCS over TLS, running on the same loop as the HTTP API and the room-call exports, and calling
the same service layer (`sublevels.service`).

It is written only from [`spec/nmdc/`](../../../../spec/nmdc/) (P1-P5, P7) and the public
documents that spec cites. Read [CLEANROOM.md](../../../../CLEANROOM.md) before working here:
implementers do not open the source of any other Direct Connect implementation. A pull request
that adds code here names the spec sections it implements.

M0 has no protocol code.
