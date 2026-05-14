import os
import json
import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from state import state
from auth import validate_token

router = APIRouter(prefix="/v1/history", tags=["History"])

class ScanHistoryRecord(BaseModel):
    job_id: str
    status: str
    overall_status: Optional[str] = "UNKNOWN"
    total_findings: int = 0
    created_at: str
    tools_used: Optional[str] = ""

class ScanHistoryResponse(BaseModel):
    scans: List[ScanHistoryRecord]

def init_history_db():
    conn = state.get_db_conn()
    cursor = conn.cursor()
    
    table_schema = """
        CREATE TABLE IF NOT EXISTS scan_history (
            job_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            overall_status TEXT,
            total_findings INTEGER DEFAULT 0,
            created_at TEXT,
            tools_used TEXT
        )
    """
    cursor.execute(table_schema)
    conn.commit()
    conn.close()
    print("[history] Scan history table initialized.")

def record_scan(job_id: str, user_id: str, status: str, report: Dict[str, Any] = None):
    try:
        conn = state.get_db_conn()
        cursor = conn.cursor()
        
        overall_status = "UNKNOWN"
        total_findings = 0
        tools_used = ""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        
        if report:
            summary = report.get("summary", {})
            overall_status = summary.get("overall_status", "UNKNOWN")
            total_findings = report.get("total_findings", 0)
        
        query = """
            INSERT INTO scan_history (job_id, user_id, status, overall_status, total_findings, created_at, tools_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """ if state.use_postgres else """
            INSERT INTO scan_history (job_id, user_id, status, overall_status, total_findings, created_at, tools_used)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        
        cursor.execute(query, (job_id, user_id, status, overall_status, total_findings, now, tools_used))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[history] Failed to record scan: {str(e)}")

@router.get("/", response_model=ScanHistoryResponse)
async def get_history(payload: dict = Depends(validate_token)):
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    
    if state.use_postgres:
        from psycopg2.extras import RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT job_id, status, overall_status, total_findings, created_at, tools_used FROM scan_history WHERE user_id = %s ORDER BY created_at DESC"
    else:
        def dict_factory(cursor, row):
            d = {}
            for idx, col in enumerate(cursor.description):
                d[col[0]] = row[idx]
            return d
        conn.row_factory = dict_factory
        cursor = conn.cursor()
        query = "SELECT job_id, status, overall_status, total_findings, created_at, tools_used FROM scan_history WHERE user_id = ? ORDER BY created_at DESC"
        
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    scans = [ScanHistoryRecord(**row) for row in rows]
    return ScanHistoryResponse(scans=scans)

@router.get("/{job_id}/report")
async def get_historical_report(job_id: str, payload: dict = Depends(validate_token)):
    """
    Proxy to fetch the full JSON report from the PVC for a historical job.
    """
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = "SELECT user_id FROM scan_history WHERE job_id = %s" if state.use_postgres else "SELECT user_id FROM scan_history WHERE job_id = ?"
    cursor.execute(query, (job_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row or row[0] != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this scan report.")
    
    return await state.backend.get_scan_report(job_id)
