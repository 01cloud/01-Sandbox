export interface GenericJob<TMetadata = any, TSummary = any> {
  job_id: string;
  job_type: "repo-scan" | "quick-scan";
  status: string;
  progress: number;
  stepMessage: string;
  eventIndex: number;
  metadata: TMetadata;
  summary: TSummary | null; // e.g. { high: 2, medium: 5, low: 10 }
  result: any | null;       // Full scan result (loaded in volatile RAM only, stripped on storage sync)
  submittedAt: string;
  completedAt: string | null;
  detail?: any;
}

export const jobStore = {
  getAll: (type?: string): GenericJob[] => {
    try {
      const list: GenericJob[] = JSON.parse(localStorage.getItem("unified_jobs_v1") || "[]");
      if (!Array.isArray(list)) return [];
      const sanitized = list.map(j => ({
        ...j,
        progress: Math.min(100, Math.max(0, typeof j.progress === "number" && !isNaN(j.progress) ? j.progress : 0))
      }));
      return type ? sanitized.filter(j => j.job_type === type) : sanitized;
    } catch (e) {
      console.error("[jobStore] Error reading from localStorage", e);
      return [];
    }
  },

  upsert: (job: GenericJob): void => {
    try {
      const all = jobStore.getAll();
      const idx = all.findIndex((j) => j.job_id === job.job_id);

      // Zero-overload rule: strip the heavy result details and clamp progress to [0, 100]
      const strippedJob: GenericJob = {
        ...job,
        progress: Math.min(100, Math.max(0, typeof job.progress === "number" && !isNaN(job.progress) ? job.progress : 0)),
        result: null
      };

      if (idx >= 0) {
        all[idx] = strippedJob;
      } else {
        all.unshift(strippedJob);
      }

      localStorage.setItem("unified_jobs_v1", JSON.stringify(all));
    } catch (e) {
      console.error("[jobStore] Error writing to localStorage", e);
    }
  },

  get: (id: string): GenericJob | null => {
    return jobStore.getAll().find(j => j.job_id === id) ?? null;
  },

  remove: (id: string): void => {
    try {
      const all = jobStore.getAll();
      const filtered = all.filter(j => j.job_id !== id);
      localStorage.setItem("unified_jobs_v1", JSON.stringify(filtered));
    } catch (e) {
      console.error("[jobStore] Error removing job", e);
    }
  }
};
