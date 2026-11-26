"""Hublist mode (roadmap D16, spec P10): a sublevels instance as a public hub list.

- ``register.py``: the registration server on port 2501 (P10 §1), the other end of P9 §1;
- ``pinger.py``: logs in to each listed hub as an NMDC client and reads what it advertises (§2);
- ``store.py``: the entries, their states and their ping history (§3);
- ``listxml.py``: the XML list clients load, plain and bz2, and one per country (§4);
- ``service.py``: the mode itself: settings, the timers, and what the HTTP API calls.
"""
