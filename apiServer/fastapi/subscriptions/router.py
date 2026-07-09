from __future__ import annotations

import datetime
import json
import os
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

DEFAULT_BACKENDS = [
    {
        "id": "Z1_SANDBOX",
        "name": "01 Sandbox",
        "description": "Production-grade hardened cluster for secure code execution.",
        "icon": "terminal",
        "color": "indigo",
        "baseUrl": "/api/v1/01sbx",
        "documentationUrl": "/api/v1/01sbx/docs",
    },
    {
        "id": "AWS_VPC_SANDBOX",
        "name": "AWS VPC Sandbox",
        "description": "Enterprise-grade isolated AWS VPC sandbox backend.",
        "icon": "box",
        "color": "emerald",
        "baseUrl": "/api/v1/awsvpc",
        "documentationUrl": "/api/v1/awsvpc/docs",
    },
]

custom_backends_json = os.environ.get("DASHBOARD_BACKENDS_JSON") or os.environ.get(
    "VITE_DASHBOARD_BACKENDS_JSON"
)
if custom_backends_json:
    try:
        raw_json = custom_backends_json.strip()
        if raw_json.startswith("'") and raw_json.endswith("'"):
            raw_json = raw_json[1:-1]
        DEFAULT_BACKENDS = json.loads(raw_json)
    except Exception as e:
        print(f"[subscriptions] Failed to parse custom backends JSON: {e}")


class SubscribeRequest(BaseModel):
    backend_id: str


def get_subscriptions_router(state, validate_token: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/backends", tags=["Backends"])
    async def list_backends(payload: dict = Depends(validate_token)):
        user_id = payload.get("sub")
        if not user_id:
            return DEFAULT_BACKENDS

        conn = state.get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT backend_id, status FROM user_subscriptions WHERE LOWER(user_id) = LOWER(%s)",
            (user_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        subscriptions = {row[0].upper(): row[1] for row in rows}

        backends_with_sub = []
        for b in DEFAULT_BACKENDS:
            # 01 Sandbox (Z1_SANDBOX) is ALWAYS subscribed by default
            is_sub = True
            if b["id"] != "Z1_SANDBOX":
                is_sub = subscriptions.get(b["id"].upper()) == "active"

            backends_with_sub.append({**b, "isSubscribed": is_sub})
        return backends_with_sub

    @router.get("/v1/subscriptions", tags=["Subscriptions"])
    async def list_subscriptions(payload: dict = Depends(validate_token)):
        user_id = payload.get("sub")
        if not user_id:
            return {"subscriptions": []}

        conn = state.get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT backend_id, status, created_at FROM user_subscriptions WHERE LOWER(user_id) = LOWER(%s)",
            (user_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        # Always include the default z1sandbox if not explicitly listed or override status to active
        subs = []
        has_z1 = False
        for row in rows:
            b_id = row[0]
            status_val = row[1]
            if b_id.upper() == "Z1_SANDBOX":
                has_z1 = True
            subs.append(
                {"backend_id": b_id, "status": status_val, "created_at": row[2]}
            )

        if not has_z1:
            subs.append(
                {
                    "backend_id": "Z1_SANDBOX",
                    "status": "active",
                    "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
                }
            )

        return {"subscriptions": subs}

    @router.post("/v1/subscriptions", tags=["Subscriptions"])
    async def subscribe_backend(
        req: SubscribeRequest, payload: dict = Depends(validate_token)
    ):
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        backend_id = req.backend_id.upper()

        # Verify valid backend ID
        valid_ids = {b["id"] for b in DEFAULT_BACKENDS}
        if backend_id not in valid_ids:
            raise HTTPException(
                status_code=400, detail=f"Invalid backend_id: {backend_id}"
            )

        now = datetime.datetime.now(datetime.UTC).isoformat()
        conn = state.get_db_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO user_subscriptions (id, user_id, backend_id, status, created_at)
                VALUES (%s, %s, %s, 'active', %s)
                ON CONFLICT (user_id, backend_id)
                DO UPDATE SET status = 'active'
                """,
                (f"sub_{user_id}_{backend_id}", user_id, backend_id, now),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        finally:
            conn.close()

        return {
            "status": "success",
            "message": f"Successfully subscribed to {backend_id}",
        }

    @router.delete("/v1/subscriptions/{backend_id}", tags=["Subscriptions"])
    async def unsubscribe_backend(
        backend_id: str, payload: dict = Depends(validate_token)
    ):
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        b_id_upper = backend_id.upper()
        if b_id_upper == "Z1_SANDBOX":
            raise HTTPException(
                status_code=400,
                detail="Cannot unsubscribe from the default sandbox backend",
            )

        conn = state.get_db_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM user_subscriptions WHERE LOWER(user_id) = LOWER(%s) AND backend_id = %s",
                (user_id, b_id_upper),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        finally:
            conn.close()

        return {
            "status": "success",
            "message": f"Successfully unsubscribed from {backend_id}",
        }

    return router
