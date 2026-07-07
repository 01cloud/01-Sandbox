import { useState, useEffect, useRef, useCallback } from "react";
import { GenericJob, jobStore } from "@/lib/jobStore";

export function useJobStore(
  jobType: "repo-scan" | "quick-scan",
  apiBase: string,
  apiKey: string
) {
  const [jobs, setJobs] = useState<GenericJob[]>(() => jobStore.getAll(jobType));

  // Cache of full scan results in volatile RAM (never persisted to localStorage)
  const [volatileResults, setVolatileResults] = useState<Record<string, any>>({});

  const esRefs = useRef<Record<string, EventSource>>({});
  const streamErrors = useRef<Record<string, number>>({});
  const deletedJobIds = useRef<Set<string>>(new Set());

  const refresh = () => {
    setJobs(jobStore.getAll(jobType));
  };

  const addJob = (job: GenericJob) => {
    jobStore.upsert(job);
    refresh();
  };

  const removeJob = async (jobId: string) => {
    // 1. Remove from local store immediately
    deletedJobIds.current.add(jobId);
    jobStore.remove(jobId);
    if (esRefs.current[jobId]) {
      esRefs.current[jobId].close();
      delete esRefs.current[jobId];
    }
    setVolatileResults(prev => {
      const copy = { ...prev };
      delete copy[jobId];
      return copy;
    });
    refresh();

    // 2. Call backend to delete job & PVC report
    if (apiKey) {
      try {
        await fetch(`${apiBase}/v1/jobs/${jobId}?purge=true`, {
          method: "DELETE",
          headers: {
            Authorization: `Bearer ${apiKey}`
          }
        });
      } catch (e) {
        console.error("[useJobStore] Failed to delete job from backend", e);
      }
    }
  };

  const lazyFetchResult = useCallback(async (jobId: string): Promise<any> => {
    if (volatileResults[jobId]) {
      return volatileResults[jobId];
    }

    if (!apiKey) return null;

    const url = jobType === "repo-scan"
      ? `${apiBase}/v1/repo-scan/${jobId}/result`
      : `${apiBase}/v1/jobs/${jobId}/result`;

    try {
      const resp = await fetch(url, {
        headers: {
          Authorization: `Bearer ${apiKey}`
        }
      });
      if (!resp.ok) throw new Error(`Failed to fetch result (HTTP ${resp.status})`);
      const data = await resp.json();

      // Cache in volatile React memory
      setVolatileResults(prev => ({
        ...prev,
        [jobId]: data
      }));
      return data;
    } catch (e) {
      console.error(`[useJobStore] Error fetching result for job ${jobId}`, e);
      return null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, apiKey, jobType]);

  const openStream = useCallback((jobId: string, since = 0) => {
    if (esRefs.current[jobId]) {
      esRefs.current[jobId].close();
    }

    if (!apiKey) return;

    const url = jobType === "repo-scan"
      ? `${apiBase}/v1/repo-scan/${jobId}/status?since=${since}&token=${encodeURIComponent(apiKey)}`
      : `${apiBase}/v1/jobs/${jobId}/status?since=${since}&token=${encodeURIComponent(apiKey)}`;
    const es = new EventSource(url);
    esRefs.current[jobId] = es;

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        const stored = jobStore.get(jobId);

        // Reset error count on successful message receipt
        streamErrors.current[jobId] = 0;

        if (!stored) {
          // If the job was deleted or not in storage, close stream
          es.close();
          delete esRefs.current[jobId];
          return;
        }

        const isTerminal = ["DONE", "ERROR"].includes(ev.step);

        // Extract and volatile-cache full result when done
        if (ev.step === "DONE") {
          // NOTE: ev.detail here contains only language-pipeline statuses (e.g. {"Go": "DONE"}),
          // NOT the full scan report. We do NOT write it into volatileResults here because
          // that would block lazyFetchResult from fetching the real rich result (with findings,
          // percentages, file counts) that powers the language distribution chart.
          // lazyFetchResult is triggered by the useEffect in RepoScannerWidget when a DONE
          // job is selected and volatileResults[jobId] is absent.
        }

        const updatedJob: GenericJob = {
          ...stored,
          status: ev.step,
          progress: Math.max(stored.progress || 0, ev.progress),
          stepMessage: ev.message,
          eventIndex: since + 1,
          summary: ev.step === "DONE" && ev.detail
            ? {
                critical: ev.detail.critical_count || 0,
                high: ev.detail.high_count || 0,
                medium: ev.detail.medium_count || 0,
                low: ev.detail.low_count || 0,
                info: ev.detail.info_count || 0
              }
            : stored.summary,
          detail: ev.detail || stored.detail,
          result: null, // Keep localStorage entry result stripped
          completedAt: ev.step === "DONE" ? new Date().toISOString() : stored.completedAt
        };

        jobStore.upsert(updatedJob);
        setJobs(jobStore.getAll(jobType));

        if (isTerminal) {
          es.close();
          delete esRefs.current[jobId];

          // Eagerly fetch the full rich result when the scan finishes so the
          // language distribution chart and per-language cards render immediately
          // without the user having to deselect and reselect the job.
          if (ev.step === "DONE") {
            lazyFetchResult(jobId);
          }
        }
      } catch (err) {
        console.error("[useJobStore] SSE parse error", err);
      }
    };

    es.onerror = () => {
      es.close();
      delete esRefs.current[jobId];

      // Increment consecutive error count
      streamErrors.current[jobId] = (streamErrors.current[jobId] || 0) + 1;

      // Fallback: poll for result if SSE drops mid-scan
      setTimeout(() => {
        lazyFetchResult(jobId).then(data => {
          if (data) {
            const stored = jobStore.get(jobId);
            if (stored && stored.status !== "DONE") {
              const updatedJob: GenericJob = {
                ...stored,
                status: "DONE",
                progress: 100,
                stepMessage: "Scan completed",
                completedAt: new Date().toISOString()
              };
              jobStore.upsert(updatedJob);
              setJobs(jobStore.getAll(jobType));
            }
          }
        });
      }, 3000);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, apiKey, jobType]);

  // ─── Server sync: discover CLI/API-triggered jobs ────────────────────────────
  //
  // URL derivation:
  //   quick-scan → GET ${apiBase}/jobs?job_type=quick-scan
  //                (intercepted by proxy/router.py before the catch-all, API-key auth)
  //   repo-scan  → GET ${apiBase}/v1/repo-scan/jobs
  //                (scan_repository/scan_repository.py, API-key auth)
  const getJobsListUrl = useCallback(() => {
    if (jobType === "quick-scan") {
      return `${apiBase}/jobs?job_type=quick-scan`;
    }
    return `${apiBase}/v1/repo-scan/jobs`;
  }, [jobType, apiBase]);

  const syncFromServer = useCallback(async () => {
    if (!apiKey) return;
    try {
      const resp = await fetch(getJobsListUrl(), {
        headers: { Authorization: `Bearer ${apiKey}` }
      });
      if (!resp.ok) {
        console.warn(`[useJobStore] syncFromServer: ${getJobsListUrl()} returned HTTP ${resp.status}`);
        return;
      }

      const serverJobs: GenericJob[] = await resp.json();
      console.debug(`[useJobStore] syncFromServer: got ${serverJobs.length} job(s) from server`);
      let didUpdate = false;

      // ── Clean up locally cached jobs that were deleted from the server ──
      const serverJobIds = new Set(serverJobs.map(sj => sj.job_id));
      const localJobs = jobStore.getAll(jobType);
      for (const lj of localJobs) {
        if (!serverJobIds.has(lj.job_id)) {
          jobStore.remove(lj.job_id);
          didUpdate = true;
        }
      }

      for (const sj of serverJobs) {
        if (deletedJobIds.current.has(sj.job_id)) {
          continue;
        }
        const existing = jobStore.get(sj.job_id);

        if (!existing) {
          // ── New job discovered externally (CLI/API-triggered) ──────────────
          jobStore.upsert({ ...sj, result: null });
          didUpdate = true;

          if (!["DONE", "ERROR", "CANCELLED"].includes(sj.status) && !esRefs.current[sj.job_id]) {
            // Active job: open SSE stream to receive live events
            openStream(sj.job_id, sj.eventIndex ?? 0);
          } else if (sj.status === "DONE") {
            // Completed job: eagerly fetch result so the report renders immediately
            lazyFetchResult(sj.job_id);
          }
        } else {
          // ── Existing job: Sync state if the SSE stream is not active ────────
          if (!esRefs.current[sj.job_id]) {
            // If the server state is different, update local state
            if (
              existing.status !== sj.status ||
              existing.progress !== sj.progress ||
              existing.stepMessage !== sj.stepMessage ||
              JSON.stringify(existing.detail) !== JSON.stringify(sj.detail)
            ) {
              const updatedJob: GenericJob = {
                ...existing,
                status: sj.status,
                progress: Math.max(existing.progress || 0, sj.progress),
                stepMessage: sj.stepMessage,
                eventIndex: sj.eventIndex ?? existing.eventIndex,
                summary: sj.summary ?? existing.summary,
                detail: sj.detail ?? existing.detail,
                completedAt: sj.completedAt ?? existing.completedAt
              };
              jobStore.upsert(updatedJob);
              didUpdate = true;

              if (sj.status === "DONE") {
                lazyFetchResult(sj.job_id);
              }
            }

            // Attempt to reconnect SSE if it's still active on the server and we haven't hit the error limit
            if (
              !["DONE", "ERROR", "CANCELLED"].includes(sj.status) &&
              (streamErrors.current[sj.job_id] || 0) < 3
            ) {
              openStream(sj.job_id, existing.eventIndex ?? 0);
            }
          }
        }
      }

      if (didUpdate) {
        setJobs(jobStore.getAll(jobType));
      }
    } catch (err) {
      console.warn("[useJobStore] syncFromServer error:", err);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, getJobsListUrl, openStream, jobType]);

  // Reconnect active streams on mount (handles page refresh mid-scan)
  useEffect(() => {
    const activeJobs = jobStore.getAll(jobType).filter(
      j => !["DONE", "ERROR", "CANCELLED"].includes(j.status)
    );
    activeJobs.forEach(j => {
      openStream(j.job_id, j.eventIndex);
    });

    return () => {
      // Cleanup all open event sources on unmount
      Object.values(esRefs.current).forEach(es => es.close());
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobType, apiKey]);

  // Poll server every 5 seconds to pick up CLI/API-triggered jobs
  useEffect(() => {
    if (!apiKey) return;

    // Sync immediately on mount or key change — don't wait 5 seconds
    syncFromServer();

    const interval = setInterval(syncFromServer, 5000);
    return () => clearInterval(interval);
  }, [syncFromServer, apiKey]);

  return {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    refresh,
    syncFromServer  // exposed so components can trigger an immediate sync after submit
  };
}
