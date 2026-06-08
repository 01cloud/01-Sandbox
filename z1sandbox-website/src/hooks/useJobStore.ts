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

  const refresh = () => {
    setJobs(jobStore.getAll(jobType));
  };

  const addJob = (job: GenericJob) => {
    jobStore.upsert(job);
    refresh();
  };

  const removeJob = (jobId: string) => {
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
  };

  const lazyFetchResult = async (jobId: string): Promise<any> => {
    if (volatileResults[jobId]) {
      return volatileResults[jobId];
    }

    if (!apiKey) return null;

    try {
      const resp = await fetch(`${apiBase}/v1/jobs/${jobId}/result`, {
        headers: {
          Authorization: `Bearer ${apiKey}`
        }
      });
      if (!resp.ok) throw new Error("Failed to fetch result");
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
  };

  const openStream = useCallback((jobId: string, since = 0) => {
    if (esRefs.current[jobId]) {
      esRefs.current[jobId].close();
    }

    if (!apiKey) return;

    const url = `${apiBase}/v1/jobs/${jobId}/status?since=${since}&token=${encodeURIComponent(apiKey)}`;
    const es = new EventSource(url);
    esRefs.current[jobId] = es;

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        const stored = jobStore.get(jobId);

        if (!stored) {
          // If the job was deleted or not in storage, close stream
          es.close();
          delete esRefs.current[jobId];
          return;
        }

        const isTerminal = ["DONE", "ERROR"].includes(ev.step);

        // Extract and volatile-cache full result when done
        if (ev.step === "DONE" && ev.detail) {
          setVolatileResults(prev => ({
            ...prev,
            [jobId]: ev.detail
          }));
        }

        const updatedJob: GenericJob = {
          ...stored,
          status: ev.step,
          progress: ev.progress,
          stepMessage: ev.message,
          eventIndex: since + 1,
          summary: ev.step === "DONE" && ev.detail
            ? {
                high: ev.detail.high_count || 0,
                medium: ev.detail.medium_count || 0,
                low: ev.detail.low_count || 0
              }
            : stored.summary,
          result: null, // Keep localStorage entry result stripped
          completedAt: ev.step === "DONE" ? new Date().toISOString() : stored.completedAt
        };

        jobStore.upsert(updatedJob);
        setJobs(jobStore.getAll(jobType));

        if (isTerminal) {
          es.close();
          delete esRefs.current[jobId];
        }
      } catch (err) {
        console.error("[useJobStore] SSE parse error", err);
      }
    };

    es.onerror = () => {
      es.close();
      delete esRefs.current[jobId];

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
      if (!resp.ok) return;

      const serverJobs: GenericJob[] = await resp.json();
      let didUpdate = false;

      for (const sj of serverJobs) {
        const existing = jobStore.get(sj.job_id);

        if (!existing) {
          // ── New job discovered externally (CLI/API-triggered) ──────────────
          jobStore.upsert({ ...sj, result: null });
          didUpdate = true;

          // Open SSE stream if the job is still active
          if (!["DONE", "ERROR"].includes(sj.status) && !esRefs.current[sj.job_id]) {
            openStream(sj.job_id, sj.eventIndex ?? 0);
          }
        } else if (
          // Reconnect lost SSE stream for an active known job (e.g. after page reload)
          !["DONE", "ERROR"].includes(existing.status) &&
          !esRefs.current[sj.job_id]
        ) {
          openStream(sj.job_id, existing.eventIndex ?? 0);
        }
      }

      if (didUpdate) {
        setJobs(jobStore.getAll(jobType));
      }
    } catch {
      // Polling is best-effort; never surface network errors to the user
    }
  }, [apiKey, getJobsListUrl, openStream, jobType]);

  // Reconnect active streams on mount (handles page refresh mid-scan)
  useEffect(() => {
    const activeJobs = jobStore.getAll(jobType).filter(
      j => !["DONE", "ERROR"].includes(j.status)
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
