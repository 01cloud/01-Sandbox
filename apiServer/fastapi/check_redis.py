import json
import os

import redis

redis_host = os.environ.get("REDIS_HOST", "localhost")
redis_port = int(os.environ.get("REDIS_PORT", 6379))
redis_pass = os.environ.get("REDIS_PASSWORD", "")

print(f"Connecting to Redis at {redis_host}:{redis_port}...")
r = redis.Redis(
    host=redis_host, port=redis_port, password=redis_pass, decode_responses=True
)

try:
    keys = r.keys("job:*")
    print(f"Found {len(keys)} job keys:")
    for k in keys:
        k_type = r.type(k)
        val = r.get(k) if k_type == "string" else f"[{k_type}]"
        print(f"  {k} -> {val}")
except Exception as e:
    print(f"Error: {e}")
