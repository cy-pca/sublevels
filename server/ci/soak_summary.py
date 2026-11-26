"""The soak's results as Markdown for a CI job summary: the ceiling first, then the rest.

    python server/ci/soak_summary.py SOAK_JSON
"""

import json
import sys

r = json.load(open(sys.argv[1]))
c, lat = r.get("ceiling") or {}, r["fanout_latency_ms"]
print(f"## Soak ({r.get('transport', 'asyncio')})\n")
print(f"- **Ceiling: {c.get('broadcasts_per_second')} broadcasts per second to "
      f"{c.get('receivers')}** ({c.get('lines')} lines in {c.get('seconds')} s; "
      f"{c.get('received')}/{c.get('expected')} delivered; hub CPU {c.get('hub_cpu_seconds')} s)")
print(f"- Online {r['online']}/{r['users']} in {r['ramp_seconds']} s; {r['kib_per_user']} KiB per "
      f"user; login p50 {r['login_seconds']['p50']} s, p95 {r['login_seconds']['p95']} s")
print(f"- Fan-out to every user, p50/p95/p99: chat {lat['chat']['p50']}/{lat['chat']['p95']}/"
      f"{lat['chat']['p99']} ms; search {lat['search']['p50']}/{lat['search']['p95']}/"
      f"{lat['search']['p99']} ms")
print(f"- Hub CPU: {r['hub_cpu_seconds']['ramp']} s over the ramp, "
      f"{r['hub_cpu_seconds']['steady_share']} of a core steady")
r.pop("samples", None)
print("\n```json\n" + json.dumps(r, indent=1) + "\n```")
