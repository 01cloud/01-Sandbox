# # API-Key-Specific Rate Limiting Module (Triggering CI/CD redeploy)
# import os
# import time

# from fastapi import HTTPException


# def rate_limit_config() -> dict:
#     """
#     Retrieves the dynamic configuration limits for key-specific rate limiting.
#     Can be dynamically tuned in Kubernetes via environment variables.
#     """
#     try:
#         requests = int(os.environ.get("RATE_LIMIT_REQUESTS", "7"))
#     except ValueError:
#         requests = 7

#     try:
#         window_secs = int(os.environ.get("RATE_LIMIT_WINDOW_SECS", "60"))
#     except ValueError:
#         window_secs = 60

#     return {"requests": requests, "window_secs": window_secs}


# async def check_rate_limit(state, jti: str):
#     """
#     Enforces a strict, dynamic key-specific rate limit using a sliding window.
#     Tracks counts dynamically in shared Redis memory (production) or
#     local memory dict (testing / local fallback) isolated per unique jti.
#     """
#     if not jti:
#         return

#     # Load thresholds
#     rl_conf = rate_limit_config()
#     requests_limit = rl_conf["requests"]
#     window_secs = rl_conf["window_secs"]

#     now_ts = time.time()
#     clear_before = now_ts - window_secs
#     retry_after = window_secs

#     current_count = 0
#     oldest_ts = None

#     # Production Mode: Shared Redis Cluster (Atomic pipeline with ZSET)
#     if getattr(state, "use_redis", False) and state.redis_client:
#         rl_key = f"ratelimit:{jti}"
#         try:
#             pipe = state.redis_client.pipeline()
#             pipe.zremrangebyscore(rl_key, 0, clear_before)
#             pipe.zcard(rl_key)
#             pipe.zrange(rl_key, 0, 0, withscores=True)
#             results = pipe.execute()

#             current_count = int(results[1])
#             oldest_elements = results[2]

#             if oldest_elements:
#                 oldest_ts = oldest_elements[0][1]
#                 retry_after = max(1, int(oldest_ts + window_secs - now_ts))
#         except Exception as e:
#             # Fallback to local logs on connection blip, don't crash core auth
#             print(
#                 f"[config] Rate limiting Redis command failed: {e}. Defaulting to unrestricted."
#             )
#             return

#         if current_count >= requests_limit:
#             print(
#                 f"[SECURITY ALERT] RATE LIMIT EXCEEDED: Key ID '{jti}' reached {current_count + 1}/{requests_limit} calls in window."
#             )
#             raise HTTPException(
#                 status_code=429,
#                 detail={
#                     "error": "Rate limit exceeded",
#                     "jti": jti,
#                     "requests_limit": requests_limit,
#                     "window_seconds": window_secs,
#                     "retry_after": retry_after,
#                 },
#                 headers={"Retry-After": str(retry_after)},
#             )

#         # If under quota, add new timestamp to the set
#         try:
#             import uuid

#             member = f"{now_ts}:{uuid.uuid4().hex}"
#             pipe = state.redis_client.pipeline()
#             pipe.zadd(rl_key, {member: now_ts})
#             pipe.expire(rl_key, window_secs * 2)
#             pipe.execute()
#         except Exception as e:
#             print(f"[config] Failed to record rate limit hit in Redis: {e}")

#     # Sandbox / Local Testing Mode: Python In-Memory Fallback
#     else:
#         if not hasattr(state, "local_rate_limits"):
#             state.local_rate_limits = {}

#         timestamps = state.local_rate_limits.get(jti, [])
#         # Filter out old timestamps
#         timestamps = [ts for ts in timestamps if ts > clear_before]
#         current_count = len(timestamps)

#         if timestamps:
#             retry_after = max(1, int(timestamps[0] + window_secs - now_ts))

#         if current_count >= requests_limit:
#             print(
#                 f"[SECURITY ALERT] RATE LIMIT EXCEEDED: Key ID '{jti}' reached {current_count + 1}/{requests_limit} calls in window."
#             )
#             raise HTTPException(
#                 status_code=429,
#                 detail={
#                     "error": "Rate limit exceeded",
#                     "jti": jti,
#                     "requests_limit": requests_limit,
#                     "window_seconds": window_secs,
#                     "retry_after": retry_after,
#                 },
#                 headers={"Retry-After": str(retry_after)},
#             )

#         # If under quota, append current timestamp and persist
#         timestamps.append(now_ts)
#         state.local_rate_limits[jti] = timestamps

#         # Self-cleaning local memory: remove old keys if dict gets large
#         if len(state.local_rate_limits) > 1000:
#             state.local_rate_limits = {
#                 k: v
#                 for k, v in state.local_rate_limits.items()
#                 if any(ts > clear_before for ts in v)
#             }


# def is_key_rate_limited(state, jti: str) -> bool:
#     """
#     Checks if a specific API key has already reached its rate limit threshold for the current sliding window.
#     Returns True if the limit is reached/exceeded, False otherwise.
#     """
#     if not jti:
#         return False

#     rl_conf = rate_limit_config()
#     requests_limit = rl_conf["requests"]
#     window_secs = rl_conf["window_secs"]

#     now_ts = time.time()
#     clear_before = now_ts - window_secs

#     current_count = 0

#     if getattr(state, "use_redis", False) and state.redis_client:
#         rl_key = f"ratelimit:{jti}"
#         try:
#             # We clean up expired keys on checking too to ensure accurate state representation
#             pipe = state.redis_client.pipeline()
#             pipe.zremrangebyscore(rl_key, 0, clear_before)
#             pipe.zcard(rl_key)
#             results = pipe.execute()
#             current_count = int(results[1])
#         except Exception:
#             pass
#     else:
#         if hasattr(state, "local_rate_limits"):
#             timestamps = state.local_rate_limits.get(jti, [])
#             timestamps = [ts for ts in timestamps if ts > clear_before]
#             # Write back clean list
#             state.local_rate_limits[jti] = timestamps
#             current_count = len(timestamps)

#     return current_count >= requests_limit


# API-Key-Specific Rate Limiting Module (Fixed-Window Click-Triggered)
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

    return {"requests": requests, "window_secs": window_secs}


async def check_rate_limit(state, jti: str):
    """
    Enforces a strict key-specific rate limit using a fixed window.
    The window timer starts EXACTLY on the first click/request event.
    Tracks counts atomically in shared Redis memory (production) or
    local memory dict (testing / local fallback) isolated per unique jti.
    """
    if not jti:
        return

    # Load thresholds
    rl_conf = rate_limit_config()
    requests_limit = rl_conf["requests"]
    window_secs = rl_conf["window_secs"]

    now_ts = time.time()
    rl_key = f"ratelimit:fixed:{jti}"

    # Production Mode: Shared Redis Cluster (Atomic Fixed-Window Counter)
    if getattr(state, "use_redis", False) and state.redis_client:
        try:
            # Atomically increment the hit counter and fetch remaining time-to-live
            pipe = state.redis_client.pipeline()
            pipe.incr(rl_key)
            pipe.ttl(rl_key)
            results = pipe.execute()

            current_count = int(results[0])
            ttl = int(results[1])

            # If this is the first request in the window, start the countdown timer
            if current_count == 1 or ttl == -1:
                state.redis_client.expire(rl_key, window_secs)
                ttl = window_secs

            # If the user exceeded the allowed budget
            if current_count > requests_limit:
                retry_after = max(1, ttl)
                print(
                    f"[SECURITY ALERT] RATE LIMIT EXCEEDED: Key ID '{jti}' reached {current_count}/{requests_limit} calls in window."
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "Rate limit exceeded",
                        "jti": jti,
                        "requests_limit": requests_limit,
                        "window_seconds": window_secs,
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            # Fallback to local logs on connection blip, don't crash core auth
            print(
                f"[config] Rate limiting Redis command failed: {e}. Defaulting to unrestricted."
            )
            return

    # Sandbox / Local Testing Mode: Python In-Memory Fixed Window Fallback
    else:
        if not hasattr(state, "local_rate_limits"):
            state.local_rate_limits = {}

        window_data = state.local_rate_limits.get(jti)

        # If window expired or doesn't exist yet, start a fresh window right now
        if not window_data or now_ts > window_data["expires_at"]:
            window_data = {"count": 0, "expires_at": now_ts + window_secs}

        # Track the click
        window_data["count"] += 1
        state.local_rate_limits[jti] = window_data

        retry_after = max(1, int(window_data["expires_at"] - now_ts))

        if window_data["count"] > requests_limit:
            print(
                f"[SECURITY ALERT] RATE LIMIT EXCEEDED: Key ID '{jti}' reached {window_data['count']}/{requests_limit} calls in window."
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "jti": jti,
                    "requests_limit": requests_limit,
                    "window_seconds": window_secs,
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        # Self-cleaning local memory: remove truly expired window keys
        if len(state.local_rate_limits) > 1000:
            state.local_rate_limits = {
                k: v
                for k, v in state.local_rate_limits.items()
                if now_ts <= v["expires_at"]
            }


def is_key_rate_limited(state, jti: str) -> bool:
    """
    Checks if a specific API key has already reached or exceeded its fixed window threshold.
    Returns True if the limit is reached/exceeded, False otherwise.
    """
    if not jti:
        return False

    rl_conf = rate_limit_config()
    requests_limit = rl_conf["requests"]
    rl_key = f"ratelimit:fixed:{jti}"

    if getattr(state, "use_redis", False) and state.redis_client:
        try:
            current_count_raw = state.redis_client.get(rl_key)
            if current_count_raw:
                return int(current_count_raw) >= requests_limit
        except Exception:
            pass
    else:
        if hasattr(state, "local_rate_limits"):
            window_data = state.local_rate_limits.get(jti)
            if window_data and time.time() <= window_data["expires_at"]:
                return window_data["count"] >= requests_limit

    return False
