import json
import os

DASHBOARDS_DIR = "/home/berrybytes/Desktop/Kamal/01-Sandbox/dashboards"
os.makedirs(DASHBOARDS_DIR, exist_ok=True)


def create_primaryhub_dashboard():
    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": True,
        "panels": [
            # ── Row 1: Executive KPI Cards (y=0, h=4) ──────────────────────
            {
                "id": 1,
                "title": "PostgreSQL Streaming Replicas",
                "type": "stat",
                "gridPos": {"x": 0, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_replication_streaming_replicas",
                        "legendFormat": "Replicas",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 2,
                "title": "PostgreSQL Replication Lag",
                "type": "stat",
                "gridPos": {"x": 4, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_replication_lag",
                        "legendFormat": "Lag",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 2},
                                {"color": "red", "value": 5},
                            ],
                        },
                    }
                },
            },
            {
                "id": 3,
                "title": "Replay Lag (Bytes)",
                "type": "stat",
                "gridPos": {"x": 8, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_stat_replication_stream_replay_lag_bytes",
                        "legendFormat": "Replay Lag",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "bytes",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 5242880},
                                {"color": "red", "value": 10485760},
                            ],
                        },
                    }
                },
            },
            {
                "id": 4,
                "title": "OCM Admission Success Rate (5m)",
                "type": "stat",
                "gridPos": {"x": 12, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": '(sum(rate(controller_runtime_webhook_requests_total{code="200"}[5m])) / sum(rate(controller_runtime_webhook_requests_total[5m])) * 100) or vector(100)',
                        "legendFormat": "Success Rate",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "yellow", "value": 99.0},
                                {"color": "green", "value": 99.9},
                            ],
                        },
                    }
                },
            },
            {
                "id": 5,
                "title": "OCM Admissions Total (200 OK)",
                "type": "stat",
                "gridPos": {"x": 16, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'sum(controller_runtime_webhook_requests_total{code="200"})',
                        "legendFormat": "Admissions",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [{"color": "blue", "value": None}],
                        },
                    }
                },
            },
            {
                "id": 6,
                "title": "OCM Admission Errors (5xx)",
                "type": "stat",
                "gridPos": {"x": 20, "y": 0, "w": 4, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'sum(controller_runtime_webhook_requests_total{code=~"5.*"}) or vector(0)',
                        "legendFormat": "Errors",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "red", "value": 1},
                            ],
                        },
                    }
                },
            },
            # ── Row 2: Header Row - PostgreSQL Streaming Replication (y=4) ─
            {
                "id": 100,
                "title": "High Availability PostgreSQL Streaming Replication & Engine",
                "type": "row",
                "gridPos": {"x": 0, "y": 4, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 7,
                "title": "PostgreSQL End-to-End Replication Lag (Target: 0s, <5s)",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_replication_lag",
                        "legendFormat": "Replication Lag (seconds)",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 15,
                        },
                    }
                },
            },
            {
                "id": 8,
                "title": "WAL Streaming Lag Breakdown: Write vs Flush vs Replay",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_stat_replication_stream_write_lag_seconds",
                        "legendFormat": "Write Lag (s)",
                        "refId": "A",
                    },
                    {
                        "expr": "cnpg_pg_stat_replication_stream_flush_lag_seconds",
                        "legendFormat": "Flush Lag (s)",
                        "refId": "B",
                    },
                    {
                        "expr": "cnpg_pg_stat_replication_stream_replay_lag_seconds",
                        "legendFormat": "Replay Lag (s)",
                        "refId": "C",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {"drawStyle": "line", "fillOpacity": 10},
                    }
                },
            },
            {
                "id": 9,
                "title": "WAL Lag in Bytes (Sent Lag vs Replay Lag)",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 13, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "cnpg_pg_stat_replication_stream_sent_lag_bytes",
                        "legendFormat": "Sent Lag Bytes",
                        "refId": "A",
                    },
                    {
                        "expr": "cnpg_pg_stat_replication_stream_replay_lag_bytes",
                        "legendFormat": "Replay Lag Bytes",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "bytes",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 10,
                "title": "Database Transactions: Commits vs Rollbacks",
                "type": "timeseries",
                "gridPos": {"x": 8, "y": 13, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(rate(cnpg_pg_stat_database_xact_commit[5m])) by (datname)",
                        "legendFormat": "Commits/s - {{datname}}",
                        "refId": "A",
                    },
                    {
                        "expr": "sum(rate(cnpg_pg_stat_database_xact_rollback[5m])) by (datname)",
                        "legendFormat": "Rollbacks/s - {{datname}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {"drawStyle": "line", "fillOpacity": 10},
                    }
                },
            },
            {
                "id": 11,
                "title": "Buffer Cache Hit Ratio & Active Connections",
                "type": "timeseries",
                "gridPos": {"x": 16, "y": 13, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "(sum(rate(cnpg_pg_stat_database_blks_hit[5m])) / (sum(rate(cnpg_pg_stat_database_blks_hit[5m])) + sum(rate(cnpg_pg_stat_database_blks_read[5m]))) * 100) or vector(100)",
                        "legendFormat": "Buffer Cache Hit %",
                        "refId": "A",
                    },
                    {
                        "expr": "sum(cnpg_pg_stat_activity_connections) by (datname)",
                        "legendFormat": "Connections - {{datname}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {"unit": "short", "custom": {"drawStyle": "line"}}
                },
            },
            # ── Row 3: Header Row - Open Cluster Management (OCM) Telemetry ─
            {
                "id": 200,
                "title": "Open Cluster Management (OCM) Webhooks & Controller SLI / SLO",
                "type": "row",
                "gridPos": {"x": 0, "y": 20, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 12,
                "title": "Admission Webhook Request Rate by Code & Path",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 21, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(rate(controller_runtime_webhook_requests_total[5m])) by (webhook, code)",
                        "legendFormat": "{{code}} - {{webhook}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "reqps",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 13,
                "title": "Webhook Latency P99 & P95 (SLO: < 200ms)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 21, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "histogram_quantile(0.99, sum(rate(controller_runtime_webhook_latency_seconds_bucket[5m])) by (le, webhook))",
                        "legendFormat": "P99: {{webhook}}",
                        "refId": "A",
                    },
                    {
                        "expr": "histogram_quantile(0.95, sum(rate(controller_runtime_webhook_latency_seconds_bucket[5m])) by (le, webhook))",
                        "legendFormat": "P95: {{webhook}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {"drawStyle": "line", "fillOpacity": 10},
                    }
                },
            },
            {
                "id": 14,
                "title": "Webhook Concurrent Requests In-Flight",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 29, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(controller_runtime_webhook_requests_in_flight) by (webhook)",
                        "legendFormat": "In Flight: {{webhook}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "line", "fillOpacity": 20},
                    }
                },
            },
            {
                "id": 15,
                "title": "Controller Workqueue Depth (Target: < 10)",
                "type": "timeseries",
                "gridPos": {"x": 8, "y": 29, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(workqueue_depth) by (name)",
                        "legendFormat": "{{name}} depth",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 16,
                "title": "Controller Workqueue Adds Rate & Retries",
                "type": "timeseries",
                "gridPos": {"x": 16, "y": 29, "w": 8, "h": 7},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(rate(workqueue_adds_total[5m])) by (name)",
                        "legendFormat": "Adds/s: {{name}}",
                        "refId": "A",
                    },
                    {
                        "expr": "sum(rate(workqueue_retries_total[5m])) by (name)",
                        "legendFormat": "Retries/s: {{name}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {"unit": "ops", "custom": {"drawStyle": "line"}}
                },
            },
            # ── Row 4: Cluster Health & Scraper Targets (y=36) ─────────────
            {
                "id": 300,
                "title": "Cluster Health & Prometheus Scrapers",
                "type": "row",
                "gridPos": {"x": 0, "y": 36, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 17,
                "title": "Prometheus Scraper Targets Health",
                "type": "stat",
                "gridPos": {"x": 0, "y": 37, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "up",
                        "legendFormat": "{{job}} ({{instance}})",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 18,
                "title": "Pod Restarts (Past 1h)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 37, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(increase(kube_pod_container_status_restarts_total[1h])) by (namespace, pod)",
                        "legendFormat": "{{namespace}} / {{pod}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "bars", "fillOpacity": 40},
                    }
                },
            },
        ],
        "refresh": "10s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["primaryhub", "postgresql", "ocm", "01sandbox"],
        "templating": {
            "list": [
                {
                    "current": {
                        "selected": True,
                        "text": "primaryhub",
                        "value": "prometheus-primaryhub",
                    },
                    "hide": 0,
                    "includeAll": False,
                    "label": "Datasource",
                    "multi": False,
                    "name": "datasource",
                    "options": [],
                    "query": "prometheus",
                    "refresh": 1,
                    "regex": ".*primaryhub.*|.*default.*",
                    "type": "datasource",
                }
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
        "timezone": "browser",
        "title": "PrimaryHub – PostgreSQL HA & OCM Observability",
        "uid": "01sandbox-primaryhub",
        "version": 1,
    }
    return dashboard


def create_secondaryhub_dashboard():
    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": True,
        "panels": [
            # ── Row 1: Executive KPI Cards ──────────────────────────────────
            {
                "id": 1,
                "title": "Standby Scraper Status",
                "type": "stat",
                "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'up{job="prometheus"} or up',
                        "legendFormat": "Scraper Status",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "ACTIVE", "color": "green"},
                                    "0": {"text": "OFFLINE", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 2,
                "title": "Standby CNPG Database Metrics",
                "type": "stat",
                "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'up{job=~"cnpg.*"}',
                        "legendFormat": "CNPG Exporter",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "STREAMING", "color": "green"},
                                    "0": {"text": "DISCONNECTED", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 3,
                "title": "Standby Active Connections",
                "type": "stat",
                "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(cnpg_pg_stat_activity_connections) or vector(0)",
                        "legendFormat": "Active Connections",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [{"color": "green", "value": None}],
                        },
                    }
                },
            },
            {
                "id": 4,
                "title": "Controller Workqueue Backlog",
                "type": "stat",
                "gridPos": {"x": 18, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(workqueue_depth) or vector(0)",
                        "legendFormat": "Backlog Items",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 5},
                                {"color": "red", "value": 20},
                            ],
                        },
                    }
                },
            },
            # ── Row 2: Secondary PostgreSQL Standby Engine ──────────────────
            {
                "id": 100,
                "title": "Secondary PostgreSQL Standby Replica Telemetry",
                "type": "row",
                "gridPos": {"x": 0, "y": 4, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 5,
                "title": "Standby Database Activity & Connections",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(cnpg_pg_stat_activity_connections) by (datname)",
                        "legendFormat": "Connections - {{datname}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 6,
                "title": "Buffer Cache Hit Ratio (%)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "(sum(rate(cnpg_pg_stat_database_blks_hit[5m])) / (sum(rate(cnpg_pg_stat_database_blks_hit[5m])) + sum(rate(cnpg_pg_stat_database_blks_read[5m]))) * 100) or vector(100)",
                        "legendFormat": "Buffer Cache Hit %",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                        "custom": {"drawStyle": "line", "fillOpacity": 10},
                    }
                },
            },
            # ── Row 3: Secondary Controllers & Workqueues ───────────────────
            {
                "id": 200,
                "title": "SecondaryHub Operators & Workqueue Health",
                "type": "row",
                "gridPos": {"x": 0, "y": 13, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 7,
                "title": "Controller Workqueue Depth",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 14, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(workqueue_depth) by (name)",
                        "legendFormat": "{{name}} depth",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 8,
                "title": "Workqueue Adds Rate & Retries",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 14, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(rate(workqueue_adds_total[5m])) by (name)",
                        "legendFormat": "Adds/s: {{name}}",
                        "refId": "A",
                    },
                    {
                        "expr": "sum(rate(workqueue_retries_total[5m])) by (name)",
                        "legendFormat": "Retries/s: {{name}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {"unit": "ops", "custom": {"drawStyle": "line"}}
                },
            },
            # ── Row 4: Cluster Health & Pod Restarts ────────────────────────
            {
                "id": 300,
                "title": "SecondaryHub Pod Restarts & Scraper Health",
                "type": "row",
                "gridPos": {"x": 0, "y": 22, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 9,
                "title": "Prometheus Scraper Targets",
                "type": "stat",
                "gridPos": {"x": 0, "y": 23, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "up",
                        "legendFormat": "{{job}} ({{instance}})",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 10,
                "title": "Pod Restarts (Past 1h)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 23, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(increase(kube_pod_container_status_restarts_total[1h])) by (namespace, pod)",
                        "legendFormat": "{{namespace}} / {{pod}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "bars", "fillOpacity": 40},
                    }
                },
            },
        ],
        "refresh": "10s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["secondaryhub", "postgresql", "standby", "01sandbox"],
        "templating": {
            "list": [
                {
                    "current": {
                        "selected": True,
                        "text": "secondaryhub",
                        "value": "prometheus-secondaryhub",
                    },
                    "hide": 0,
                    "includeAll": False,
                    "label": "Datasource",
                    "multi": False,
                    "name": "datasource",
                    "options": [],
                    "query": "prometheus",
                    "refresh": 1,
                    "regex": ".*secondaryhub.*",
                    "type": "datasource",
                }
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "timezone": "browser",
        "title": "SecondaryHub – Standby PostgreSQL & Secondary Observability",
        "uid": "01sandbox-secondaryhub",
        "version": 1,
    }
    return dashboard


def create_spoke_dashboard(spoke_name, spoke_uid):
    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": True,
        "panels": [
            # ── Row 1: Executive KPI Cards ──────────────────────────────────
            {
                "id": 1,
                "title": "OCM Klusterlet Agent Health",
                "type": "stat",
                "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'up{job="open-cluster-management-agent"} or up{job=~".*agent.*"}',
                        "legendFormat": "Agent Health",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "HEALTHY", "color": "green"},
                                    "0": {"text": "DEGRADED", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 2,
                "title": "Prometheus Scraper Health",
                "type": "stat",
                "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": 'up{job="prometheus"} or up',
                        "legendFormat": "Scraper",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "ONLINE", "color": "green"},
                                    "0": {"text": "OFFLINE", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 3,
                "title": "Active Workqueue Backlog",
                "type": "stat",
                "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(workqueue_depth) or vector(0)",
                        "legendFormat": "Queue Depth",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 5},
                                {"color": "red", "value": 20},
                            ],
                        },
                    }
                },
            },
            {
                "id": 4,
                "title": "Total Restarts (Past 1h)",
                "type": "stat",
                "gridPos": {"x": 18, "y": 0, "w": 6, "h": 4},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(increase(kube_pod_container_status_restarts_total[1h])) or vector(0)",
                        "legendFormat": "Restarts",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "area",
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "red", "value": 1},
                            ],
                        },
                    }
                },
            },
            # ── Row 2: OCM Klusterlet Agent & Workqueues ────────────────────
            {
                "id": 100,
                "title": "OCM Klusterlet Telemetry & Controller Workqueues",
                "type": "row",
                "gridPos": {"x": 0, "y": 4, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 5,
                "title": "Klusterlet Workqueue Depth by Worker",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(workqueue_depth) by (name)",
                        "legendFormat": "{{name}} depth",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 6,
                "title": "Workqueue Event Rate (Adds/s) vs Retries",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 5, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(rate(workqueue_adds_total[5m])) by (name)",
                        "legendFormat": "Adds/s: {{name}}",
                        "refId": "A",
                    },
                    {
                        "expr": "sum(rate(workqueue_retries_total[5m])) by (name)",
                        "legendFormat": "Retries/s: {{name}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {"unit": "ops", "custom": {"drawStyle": "line"}}
                },
            },
            # ── Row 3: Cluster Pod Health & Scraper Targets ─────────────────
            {
                "id": 200,
                "title": "Cluster Pod Health & Prometheus Scrapers",
                "type": "row",
                "gridPos": {"x": 0, "y": 13, "w": 24, "h": 1},
                "collapsed": False,
            },
            {
                "id": 7,
                "title": "Prometheus Scrape Targets Status",
                "type": "stat",
                "gridPos": {"x": 0, "y": 14, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "up",
                        "legendFormat": "{{job}} ({{instance}})",
                        "refId": "A",
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            {
                "id": 8,
                "title": "Pod Restarts over Time",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 14, "w": 12, "h": 6},
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "targets": [
                    {
                        "expr": "sum(increase(kube_pod_container_status_restarts_total[1h])) by (namespace, pod)",
                        "legendFormat": "{{namespace}} / {{pod}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {"drawStyle": "bars", "fillOpacity": 40},
                    }
                },
            },
        ],
        "refresh": "10s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": [spoke_name, "spoke", "klusterlet", "01sandbox"],
        "templating": {
            "list": [
                {
                    "current": {
                        "selected": True,
                        "text": spoke_name,
                        "value": spoke_uid,
                    },
                    "hide": 0,
                    "includeAll": False,
                    "label": "Datasource",
                    "multi": False,
                    "name": "datasource",
                    "options": [],
                    "query": "prometheus",
                    "refresh": 1,
                    "regex": f".*{spoke_name}.*",
                    "type": "datasource",
                }
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "timezone": "browser",
        "title": f"{spoke_name.capitalize()} – OCM Klusterlet & Edge Observability",
        "uid": f"01sandbox-{spoke_name}",
        "version": 1,
    }
    return dashboard


def create_multi_cluster_overview_dashboard():
    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": True,
        "panels": [
            # ── Row 1: Multi-Cluster Scraper Health ─────────────────────────
            {
                "id": 1,
                "title": "Prometheus Scraper Status Across All Clusters",
                "type": "stat",
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 4},
                "targets": [
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-primaryhub",
                        },
                        "expr": "up",
                        "legendFormat": "primaryhub: {{job}}",
                        "refId": "A",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-secondaryhub",
                        },
                        "expr": "up",
                        "legendFormat": "secondaryhub: {{job}}",
                        "refId": "B",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke1",
                        },
                        "expr": "up",
                        "legendFormat": "spoke1: {{job}}",
                        "refId": "C",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke2",
                        },
                        "expr": "up",
                        "legendFormat": "spoke2: {{job}}",
                        "refId": "D",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke3",
                        },
                        "expr": "up",
                        "legendFormat": "spoke3: {{job}}",
                        "refId": "E",
                    },
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": 0},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
            },
            # ── Row 2: PostgreSQL HA & OCM Hub Core SLIs ───────────────────
            {
                "id": 2,
                "title": "PostgreSQL HA Replication Lag (PrimaryHub → SecondaryHub)",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 4, "w": 12, "h": 8},
                "targets": [
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-primaryhub",
                        },
                        "expr": "cnpg_pg_replication_lag",
                        "legendFormat": "Replication Lag (s)",
                        "refId": "A",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-primaryhub",
                        },
                        "expr": "cnpg_pg_stat_replication_stream_replay_lag_bytes / 1048576",
                        "legendFormat": "Replay Lag (MB)",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "overrides": [
                        {
                            "matcher": {
                                "id": "byName",
                                "options": "Replication Lag (s)",
                            },
                            "properties": [{"id": "unit", "value": "s"}],
                        },
                        {
                            "matcher": {"id": "byName", "options": "Replay Lag (MB)"},
                            "properties": [{"id": "unit", "value": "MB"}],
                        },
                    ]
                },
            },
            {
                "id": 3,
                "title": "OCM Admission Webhook Success Rate (Primary vs Secondary)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 4, "w": 12, "h": 8},
                "targets": [
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-primaryhub",
                        },
                        "expr": 'sum(rate(controller_runtime_webhook_requests_total{code="200"}[5m])) / sum(rate(controller_runtime_webhook_requests_total[5m]))',
                        "legendFormat": "PrimaryHub Success Rate",
                        "refId": "A",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-secondaryhub",
                        },
                        "expr": 'sum(rate(controller_runtime_webhook_requests_total{code="200"}[5m])) / sum(rate(controller_runtime_webhook_requests_total[5m]))',
                        "legendFormat": "SecondaryHub Success Rate",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {"unit": "percentunit", "min": 0, "max": 1}
                },
            },
            # ── Row 3: Spoke Klusterlet Health & Pod Restarts ──────────────
            {
                "id": 4,
                "title": "OCM Klusterlet Agent Health – All Spoke Clusters",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 12, "w": 12, "h": 7},
                "targets": [
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke1",
                        },
                        "expr": 'up{job="open-cluster-management-agent"} or up',
                        "legendFormat": "Spoke1 Agent",
                        "refId": "A",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke2",
                        },
                        "expr": 'up{job="open-cluster-management-agent"} or up',
                        "legendFormat": "Spoke2 Agent",
                        "refId": "B",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke3",
                        },
                        "expr": 'up{job="open-cluster-management-agent"} or up',
                        "legendFormat": "Spoke3 Agent",
                        "refId": "C",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ]
                    }
                },
            },
            {
                "id": 5,
                "title": "Pod Restarts (All 5 Clusters)",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 12, "w": 12, "h": 7},
                "targets": [
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-primaryhub",
                        },
                        "expr": "increase(kube_pod_container_status_restarts_total[1h])",
                        "legendFormat": "primaryhub: {{namespace}}/{{pod}}",
                        "refId": "A",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-secondaryhub",
                        },
                        "expr": "increase(kube_pod_container_status_restarts_total[1h])",
                        "legendFormat": "secondaryhub: {{namespace}}/{{pod}}",
                        "refId": "B",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke1",
                        },
                        "expr": "increase(kube_pod_container_status_restarts_total[1h])",
                        "legendFormat": "spoke1: {{namespace}}/{{pod}}",
                        "refId": "C",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke2",
                        },
                        "expr": "increase(kube_pod_container_status_restarts_total[1h])",
                        "legendFormat": "spoke2: {{namespace}}/{{pod}}",
                        "refId": "D",
                    },
                    {
                        "datasource": {
                            "type": "prometheus",
                            "uid": "prometheus-spoke3",
                        },
                        "expr": "increase(kube_pod_container_status_restarts_total[1h])",
                        "legendFormat": "spoke3: {{namespace}}/{{pod}}",
                        "refId": "E",
                    },
                ],
            },
        ],
        "refresh": "15s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["multi-cluster", "overview", "01sandbox"],
        "time": {"from": "now-1h", "to": "now"},
        "timezone": "browser",
        "title": "01-Sandbox Multi-Cluster Central Overview",
        "uid": "01sandbox-overview",
        "version": 1,
    }
    return dashboard


# Generate and save all dashboard JSON files
dashboards = {
    "primaryhub.json": create_primaryhub_dashboard(),
    "secondaryhub.json": create_secondaryhub_dashboard(),
    "spoke1.json": create_spoke_dashboard("spoke1", "prometheus-spoke1"),
    "spoke2.json": create_spoke_dashboard("spoke2", "prometheus-spoke2"),
    "spoke3.json": create_spoke_dashboard("spoke3", "prometheus-spoke3"),
    "multi-cluster-overview.json": create_multi_cluster_overview_dashboard(),
}

for filename, content in dashboards.items():
    filepath = os.path.join(DASHBOARDS_DIR, filename)
    with open(filepath, "w") as f:
        json.dump(content, f, indent=2)
    print(f"Generated {filepath}")
