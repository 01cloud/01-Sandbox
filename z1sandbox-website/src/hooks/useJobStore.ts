import { useState, useEffect, useRef } from "react";
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

  const openStream = (jobId: string, since = 0) => {
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
          // If the job was deleted or not in storage, ignore or stop
          es.close();
          delete esRefs.current[jobId];
          return;
        }

        const isTerminal = ["DONE", "ERROR"].includes(ev.step);

        // Extract and volatile-cache full result if done
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
          summary: ev.step === "DONE" && ev.detail ? {
            high: ev.detail.high_count || 0,
            medium: ev.detail.medium_count || 0,
            low: ev.detail.low_count || 0
          } : stored.summary,
          result: null, // Keep localStorage entry result stripped
          completedAt: ev.step === "DONE" ? new Date().toISOString() : stored.completedAt
        };

        jobStore.upsert(updatedJob);
        refresh();

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

      // Fallback: Check if job completed in the background and fetch result
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
              refresh();
            }
          }
        });
      }, 3000);
    };
  };

  // Reconnect active streams on mount
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
  }, [jobType, apiKey]);

  return {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    refresh
  };
}
