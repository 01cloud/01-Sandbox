import React from "react";
import { GenericJob } from "@/lib/jobStore";
import { Github, FileText, CheckCircle2, AlertCircle, Loader2, Trash2, StopCircle } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface JobsPanelProps {
  jobs: GenericJob[];
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
  onDeleteJob: (jobId: string) => void;
  jobType: "repo-scan" | "quick-scan";
  embedded?: boolean;
}

export function JobsPanel({
  jobs,
  selectedJobId,
  onSelectJob,
  onDeleteJob,
  jobType,
  embedded = false,
}: JobsPanelProps) {
  const [expandedPipelines, setExpandedPipelines] = React.useState<Record<string, boolean>>({});

  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  return (
    <div className={cn(
      "flex flex-col h-full transition-all duration-300",
      embedded
        ? "w-full border-t border-border bg-transparent"
        : "w-[260px] shrink-0 border-r border-border bg-muted/10 animate-in slide-in-from-left duration-300"
    )}>
      {/* Header */}
      {!embedded && (
        <div className="p-5 border-b border-border shrink-0">
          <h3 className="text-sm font-black uppercase tracking-wider text-foreground">
            {jobType === "repo-scan" ? "Repository Scans" : "Quick Scans"}
          </h3>
          <p className="text-[10px] font-bold text-muted-foreground/50 uppercase tracking-wider">
            Recent Executions ({jobs.length})
          </p>
        </div>
      )}

      {/* Scrollable list of jobs */}
      <ScrollArea className="flex-1">
        {jobs.length === 0 ? (
          <div className="p-8 text-center text-xs font-semibold text-muted-foreground/40 flex flex-col items-center justify-center h-[200px]">
            No recent scans
          </div>
        ) : (
          <div className="divide-y divide-border">
            {jobs.map((job) => {
              const isSelected = job.job_id === selectedJobId;
              const isActive = !["DONE", "ERROR"].includes(job.status);
              const isDone = job.status === "DONE";
              const isError = job.status === "ERROR";
              const isCancelled = job.status === "CANCELLED";

              // Metadata displays
              const repoUrl = job.metadata?.repo_url || "";
              const repoName = repoUrl
                ? repoUrl.split("github.com/")[1] || repoUrl
                : `Scan Job #${job.job_id.slice(0, 6)}`;
              const filesCount = job.metadata?.files_count || 0;

              // Language pipeline should collapse on DONE/ERROR/CANCELLED, but show if active or explicitly clicked
              const showPipeline = isSelected && (isActive ? true : !!expandedPipelines[job.job_id]);

              return (
                <div
                  key={job.job_id}
                  className={cn(
                    "group relative border-l-2 border-l-transparent hover:bg-muted/20 transition-all duration-200 cursor-pointer p-4 flex flex-col gap-1.5",
                    isSelected && (jobType === "repo-scan" ? "border-l-violet-500 bg-violet-500/[0.03]" : "border-l-blue-500 bg-blue-500/[0.03]")
                  )}
                  onClick={() => {
                    onSelectJob(job.job_id);
                    if (isSelected && !isActive) {
                      setExpandedPipelines(prev => ({
                        ...prev,
                        [job.job_id]: !prev[job.job_id]
                      }));
                    }
                  }}
                >
                  {/* Top line: Type Icon + Name */}
                  <div className="flex items-start gap-2.5">
                    <div
                      className={cn(
                        "p-1.5 rounded-lg border text-muted-foreground",
                        jobType === "repo-scan" ? "bg-violet-500/10 border-violet-500/10 text-violet-400" : "bg-blue-500/10 border-blue-500/10 text-blue-400",
                        isSelected && (jobType === "repo-scan" ? "border-violet-500/20" : "border-blue-500/20")
                      )}
                    >
                      {jobType === "repo-scan" ? (
                        <Github className="w-3.5 h-3.5" />
                      ) : (
                        <FileText className="w-3.5 h-3.5" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-bold text-foreground/90 truncate leading-snug">
                        {repoName}
                      </p>
                      <span className="text-[9px] text-muted-foreground/60 block mt-0.5">
                        {jobType === "repo-scan" ? "GitHub" : `${filesCount} files`} · {formatTime(job.submittedAt)}
                      </span>
                    </div>

                    {/* Delete button (only visible on hover to keep UI clean) */}
                    <button
                      className="opacity-0 group-hover:opacity-100 hover:text-destructive p-1 rounded-md hover:bg-destructive/10 transition-all duration-150 shrink-0 text-muted-foreground/50 self-start"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteJob(job.job_id);
                      }}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {/* Status row */}
                  <div className="flex items-center gap-1.5 mt-1 border-t border-border pt-2.5">
                    {isDone ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                    ) : isError ? (
                      <AlertCircle className="w-3 h-3 text-destructive shrink-0" />
                    ) : isCancelled ? (
                      <StopCircle className="w-3 h-3 text-orange-500 shrink-0" />
                    ) : (
                      <Loader2 className="w-3 h-3 text-violet-500 animate-spin shrink-0" />
                    )}

                    <span
                      className={cn(
                        "text-[9px] font-bold uppercase tracking-wider truncate flex-1",
                        isDone && "text-emerald-500",
                        isError && "text-destructive",
                        isCancelled && "text-orange-500",
                        isActive && !isCancelled && (jobType === "repo-scan" ? "text-violet-400" : "text-blue-400")
                      )}
                    >
                      {isActive ? `${job.status} (${job.progress}%)` : job.status}
                    </span>

                    {/* Severity Summary counts badge */}
                    {isDone && job.summary && (
                      <div className="flex gap-1 shrink-0">
                        {job.summary.critical > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-red-600/25 hover:bg-red-600/25 text-red-500 border border-red-500/35 font-extrabold rounded-md">
                            C:{job.summary.critical}
                          </Badge>
                        )}
                        {job.summary.high > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-orange-500/20 hover:bg-orange-500/20 text-orange-500 border border-orange-500/30 font-extrabold rounded-md">
                            H:{job.summary.high}
                          </Badge>
                        )}
                        {job.summary.medium > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-yellow-500/20 hover:bg-yellow-500/20 text-yellow-500 border border-yellow-500/30 font-extrabold rounded-md">
                            M:{job.summary.medium}
                          </Badge>
                        )}
                        {job.summary.low > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-blue-500/20 hover:bg-blue-500/20 text-blue-500 border border-blue-500/30 font-extrabold rounded-md">
                            L:{job.summary.low}
                          </Badge>
                        )}
                        {job.summary.info > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-slate-500/20 hover:bg-slate-500/20 text-slate-400 border border-slate-500/30 font-extrabold rounded-md">
                            I:{job.summary.info}
                          </Badge>
                        )}
                        {(job.summary.critical ?? 0) === 0 && job.summary.high === 0 && job.summary.medium === 0 && job.summary.low === 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-emerald-500/20 hover:bg-emerald-500/20 text-emerald-500 border border-emerald-500/30 font-extrabold rounded-md">
                            SECURE
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Expanded language statuses for selected repository scans */}
                  {showPipeline && jobType === "repo-scan" && job.detail?.languages && Object.keys(job.detail.languages).length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border flex flex-col gap-2">
                      <p className="text-[9px] font-black uppercase tracking-wider text-muted-foreground mb-1">
                        Language Pipeline
                      </p>
                      <div className="flex flex-col gap-1.5">
                        {Object.entries(job.detail.languages).map(([lang, status]: [string, any]) => {
                          const statusStr = typeof status === "string" ? status : "DONE";
                          const isPending = statusStr === "PENDING";
                          const isScanningLang = statusStr === "SCANNING";
                          const isDone = statusStr === "DONE" || statusStr === "COMPLETE";
                          const isFailed = statusStr === "FAILED";

                          return (
                            <div key={lang} className="flex items-center justify-between text-[11px]">
                              <span className="font-bold text-foreground/80">{lang}</span>
                              <span
                                className={cn(
                                  "text-[8px] font-extrabold uppercase tracking-wide flex items-center gap-1",
                                  isPending && "text-muted-foreground/60",
                                  isScanningLang && "text-violet-400 animate-pulse",
                                  isDone && "text-emerald-500",
                                  isFailed && "text-destructive"
                                )}
                              >
                                {isScanningLang && <Loader2 className="w-2.5 h-2.5 animate-spin shrink-0" />}
                                {isPending ? "Pending" : isScanningLang ? "Scanning" : isDone ? "Complete" : "Failed"}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
