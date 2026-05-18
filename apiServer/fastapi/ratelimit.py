# API-Key-Specific Rate Limiting Module (Triggering CI/CD redeploy)
import os
import time
from fastapi import HTTPException

def rate_limit_config() -> dict:
    """
    Retrieves the dynamic configuration limits for key-specific rate limiting.
    Can be dynamically tuned in Kubernetes via environment variables.
    """
    try:
        requests = int(os.environ.get("RATE_LIMIT_REQUESTS", "7"))
    except ValueError:
        requests = 7

    try:
        window_secs = int(os.environ.get("RATE_LIMIT_WINDOW_SECS", "60"))
    except ValueError:
        window_secs = 60

    return {
        "requests": requests,
        "window_secs": window_secs
    }

async def check_rate_limit(state, jti: str):
    """
    Enforces a strict, dynamic key-specific rate limit.
    Tracks counts dynamically in shared Redis memory (production) or
    local memory dict (testing / local fallback) isolated per unique jti.
    """
    if not jti:
        return

    # Load thresholds
    rl_conf = rate_limit_config()
    requests_limit = rl_conf["requests"]
    window_secs = rl_conf["window_secs"]

    now_ts = int(time.time())
    window_bucket = now_ts // window_secs
    retry_after = window_secs - (now_ts % window_secs)

    current_count = 0

    # Production Mode: Shared Redis Cluster (Atomic pipeline INCR + EXPIRE)
    if getattr(state, "use_redis", False) and state.redis_client:
        rl_key = f"ratelimit:{jti}:{window_bucket}"
        try:
            pipe = state.redis_client.pipeline()
            pipe.incr(rl_key)
            pipe.expire(rl_key, window_secs * 2)  # Persist long enough to transition
            results = pipe.execute()
            current_count = int(results[0])
        except Exception as e:
            # Fallback to local logs on connection blip, don't crash core auth
            print(f"[config] Rate limiting Redis command failed: {e}. Defaulting to unrestricted.")
            return
    
    # Sandbox / Local Testing Mode: Python In-Memory Fallback
    else:
        if not hasattr(state, "local_rate_limits"):
            state.local_rate_limits = {}

        rl_key = f"{jti}:{window_bucket}"
        current_count = state.local_rate_limits.get(rl_key, 0) + 1
        state.local_rate_limits[rl_key] = current_count

        # Self-cleaning local memory: remove old keys if dict gets large
        if len(state.local_rate_limits) > 1000:
            state.local_rate_limits = {
                k: v for k, v in state.local_rate_limits.items() 
                if k.endswith(str(window_bucket))
            }

    # Quota evaluation
    if current_count > requests_limit:
        print(f"[SECURITY ALERT] RATE LIMIT EXCEEDED: Key ID '{jti}' reached {current_count}/{requests_limit} calls in window.")
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "jti": jti,
                "requests_limit": requests_limit,
                "window_seconds": window_secs,
                "retry_after": retry_after
            },
            headers={"Retry-After": str(retry_after)}
        )

def is_key_rate_limited(state, jti: str) -> bool:
    """
    Checks if a specific API key has already reached its rate limit threshold for the current window.
    Returns True if the limit is reached/exceeded, False otherwise.
    """
    if not jti:
        return False
    
    rl_conf = rate_limit_config()
    requests_limit = rl_conf["requests"]
    window_secs = rl_conf["window_secs"]
    
    now_ts = int(time.time())
    window_bucket = now_ts // window_secs
    
    current_count = 0
    
    if getattr(state, "use_redis", False) and state.redis_client:
        rl_key = f"ratelimit:{jti}:{window_bucket}"
        try:
            val = state.redis_client.get(rl_key)
            if val is not None:
                current_count = int(val)
        except Exception:
            pass
    else:
        if hasattr(state, "local_rate_limits"):
            rl_key = f"{jti}:{window_bucket}"
            current_count = state.local_rate_limits.get(rl_key, 0)
            
    return current_count >= requests_limit
