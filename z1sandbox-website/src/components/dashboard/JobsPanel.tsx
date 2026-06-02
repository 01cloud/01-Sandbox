import React from "react";
import { GenericJob } from "@/lib/jobStore";
import { Github, FileText, CheckCircle2, AlertCircle, Loader2, Trash2 } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface JobsPanelProps {
  jobs: GenericJob[];
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
  onDeleteJob: (jobId: string) => void;
  jobType: "repo-scan" | "quick-scan";
}

export function JobsPanel({
  jobs,
  selectedJobId,
  onSelectJob,
  onDeleteJob,
  jobType,
}: JobsPanelProps) {
  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  return (
    <div className="w-[320px] shrink-0 border-r border-border/50 bg-muted/10 flex flex-col h-full animate-in slide-in-from-left duration-300">
      {/* Header */}
      <div className="p-5 border-b border-border/50 flex flex-col gap-1 shrink-0">
        <h3 className="text-sm font-black uppercase tracking-wider text-foreground">
          {jobType === "repo-scan" ? "Repository Scans" : "Quick Scans"}
        </h3>
        <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
          Recent Executions ({jobs.length})
        </p>
      </div>

      {/* Scrollable list of jobs */}
      <ScrollArea className="flex-1">
        {jobs.length === 0 ? (
          <div className="p-8 text-center text-xs font-semibold text-muted-foreground/40 flex flex-col items-center justify-center h-[200px]">
            No recent scans
          </div>
        ) : (
          <div className="p-3.5 space-y-2">
            {jobs.map((job) => {
              const isSelected = job.job_id === selectedJobId;
              const isActive = !["DONE", "ERROR"].includes(job.status);
              const isDone = job.status === "DONE";
              const isError = job.status === "ERROR";

              // Metadata displays
              const repoUrl = job.metadata?.repo_url || "";
              const repoName = repoUrl
                ? repoUrl.split("github.com/")[1] || repoUrl
                : `Scan Job #${job.job_id.slice(0, 6)}`;
              const filesCount = job.metadata?.files_count || 0;

              return (
                <div
                  key={job.job_id}
                  className={cn(
                    "group relative rounded-xl border border-border/40 bg-card/40 hover:bg-card hover:border-violet-500/30 transition-all duration-200 cursor-pointer overflow-hidden p-3.5 flex flex-col gap-1.5",
                    isSelected && "border-violet-500/60 bg-violet-500/5 shadow-md shadow-violet-500/5 hover:bg-violet-500/5 hover:border-violet-500/60"
                  )}
                  onClick={() => onSelectJob(job.job_id)}
                >
                  {/* Top line: Type Icon + Name */}
                  <div className="flex items-start gap-2.5">
                    <div
                      className={cn(
                        "p-1.5 rounded-lg border text-muted-foreground",
                        jobType === "repo-scan" ? "bg-violet-500/10 border-violet-500/10 text-violet-400" : "bg-blue-500/10 border-blue-500/10 text-blue-400",
                        isSelected && "border-violet-500/20"
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
                  <div className="flex items-center gap-1.5 mt-1 border-t border-border/20 pt-2.5">
                    {isDone ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                    ) : isError ? (
                      <AlertCircle className="w-3 h-3 text-destructive shrink-0" />
                    ) : (
                      <Loader2 className="w-3 h-3 text-violet-500 animate-spin shrink-0" />
                    )}

                    <span
                      className={cn(
                        "text-[9px] font-bold uppercase tracking-wider truncate flex-1",
                        isDone && "text-emerald-500",
                        isError && "text-destructive",
                        isActive && "text-violet-400"
                      )}
                    >
                      {isActive ? `${job.status} (${job.progress}%)` : job.status}
                    </span>

                    {/* Severity Summary counts badge */}
                    {isDone && job.summary && (
                      <div className="flex gap-1 shrink-0">
                        {job.summary.high > 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-red-500/20 hover:bg-red-500/20 text-red-500 border border-red-500/30 font-extrabold rounded-md">
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
                        {job.summary.high === 0 && job.summary.medium === 0 && job.summary.low === 0 && (
                          <Badge className="h-4 px-1 text-[8px] bg-emerald-500/20 hover:bg-emerald-500/20 text-emerald-500 border border-emerald-500/30 font-extrabold rounded-md">
                            SECURE
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
