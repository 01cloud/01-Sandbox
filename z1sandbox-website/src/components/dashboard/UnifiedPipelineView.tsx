import React from "react";
import { CheckCircle2, AlertCircle, Loader2, Circle, RotateCw, XCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { GenericJob } from "@/lib/jobStore";

export interface PipelineStepConfig {
  key: string;
  label: string;
}

interface UnifiedPipelineViewProps {
  job: GenericJob;
  steps: PipelineStepConfig[];
  result: any | null;
  onResultRender: (result: any) => React.ReactNode;
  onCancel?: (jobId: string) => void;
}

export function UnifiedPipelineView({
  job,
  steps,
  result,
  onResultRender,
  onCancel,
}: UnifiedPipelineViewProps) {
  const currentStep = job.status;
  const currentIdx = steps.findIndex((s) => s.key === currentStep);
  const isRetrying = currentStep === "RETRYING";
  const activeIdx = isRetrying
    ? (steps.findIndex(s => s.key === "SCANNING") !== -1 ? steps.findIndex(s => s.key === "SCANNING") : 1)
    : currentIdx;

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Pipeline Step Timeline ── */}
      <div className="rounded-2xl border border-border/30 bg-[hsl(var(--card))]/60 backdrop-blur-sm overflow-hidden">
        {/* Section header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <label className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
            Scan Pipeline Execution
          </label>
          <div className="flex items-center gap-2">
            {job.status === "DONE" && (
              <span className="text-[9px] font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider">
                Completed
              </span>
            )}
            {job.status === "ERROR" && (
              <span className="text-[9px] font-bold text-red-400 bg-red-500/10 border border-red-500/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider">
                Failed
              </span>
            )}
          </div>
        </div>

        {/* Timeline */}
        <div className="px-6 pb-5">
          <div className="relative">
            {/* Vertical connector line */}
            <div className="absolute left-[13px] top-4 bottom-4 w-px bg-gradient-to-b from-border/60 via-border/30 to-transparent" />

            <div className="flex flex-col gap-1">
              {steps.map((step, idx) => {
                const isFailedStep = currentStep === "ERROR" && idx === Math.max(0, activeIdx);
                const isDone = currentStep === "DONE" || (activeIdx !== -1 && idx < activeIdx);
                const isActive = idx === activeIdx && !isFailedStep && !isRetrying;
                const isActiveRetry = isRetrying && idx === activeIdx;
                const isPending = !isDone && !isActive && !isActiveRetry && !isFailedStep;

                return (
                  <div
                    key={step.key}
                    className={cn(
                      "relative flex items-start gap-4 py-2.5 px-3 rounded-xl transition-all duration-300",
                      isActive && "bg-violet-500/8",
                      isActiveRetry && "bg-amber-500/8",
                      isFailedStep && "bg-red-500/8",
                    )}
                  >
                    {/* Step circle indicator */}
                    <div className="relative z-10 mt-0.5">
                      {isDone ? (
                        <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.15)]">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        </div>
                      ) : isFailedStep ? (
                        <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-red-500/15 border border-red-500/40 text-red-400 shadow-[0_0_12px_rgba(239,68,68,0.2)]">
                          <AlertCircle className="w-3.5 h-3.5" />
                        </div>
                      ) : isActiveRetry ? (
                        <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-amber-500/15 border border-amber-500/30 text-amber-400 shadow-[0_0_12px_rgba(245,158,11,0.25)] animate-pulse">
                          <RotateCw className="w-3.5 h-3.5 animate-spin" />
                        </div>
                      ) : isActive ? (
                        <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-violet-500/15 border border-violet-500/40 text-violet-400 shadow-[0_0_16px_rgba(139,92,246,0.3)]">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        </div>
                      ) : (
                        <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center border border-border/40 bg-muted/20 text-muted-foreground/25">
                          <Circle className="w-2 h-2 fill-current" />
                        </div>
                      )}
                    </div>

                    {/* Step content */}
                    <div className="flex-1 min-w-0 pt-0.5">
                      <span
                        className={cn(
                          "text-[13px] font-semibold transition-colors leading-tight",
                          isDone && "text-emerald-400/90",
                          isActive && "text-foreground",
                          isActiveRetry && "text-amber-400",
                          isFailedStep && "text-red-400",
                          isPending && "text-muted-foreground/35"
                        )}
                      >
                        {step.label}
                      </span>

                      {/* Active step substatus */}
                      {isActive && job.stepMessage && (
                        <p className="text-[11px] text-muted-foreground/70 mt-1 font-mono leading-relaxed animate-pulse">
                          {job.stepMessage}
                        </p>
                      )}
                      {isActiveRetry && job.stepMessage && (
                        <p className="text-[11px] text-amber-400/70 mt-1 font-mono leading-relaxed animate-pulse">
                          {job.stepMessage}
                        </p>
                      )}
                      {isFailedStep && job.stepMessage && (
                        <p className="text-[11px] text-red-400/70 mt-1 font-mono leading-relaxed">
                          {job.stepMessage}
                        </p>
                      )}

                      {/* Terminal log area for the active scanning step */}
                      {isActive && step.key === "SCANNING" && job.stepMessage && (
                        <div className="mt-3 rounded-lg bg-[#0d0d1a] border border-border/20 p-3 font-mono text-[10px] leading-relaxed text-emerald-400/80 max-h-[120px] overflow-y-auto shadow-inner">
                          <div className="flex items-center gap-2 text-muted-foreground/40 mb-2">
                            <span className="text-[9px]">❯</span>
                            <span>Processing languages: Semgrep, Bandit & Enry engines...</span>
                          </div>
                          <div className="text-violet-300/60 whitespace-pre-wrap break-all">
                            {job.stepMessage}
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Right-side status badge */}
                    <div className="shrink-0 mt-1">
                      {isDone && (
                        <span className="text-[8px] font-bold text-emerald-500/60 uppercase tracking-widest">✓</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* ── Overall Progress ── */}
      <div className="rounded-2xl border border-border/30 bg-[hsl(var(--card))]/60 backdrop-blur-sm p-5">
        <div className="flex justify-between items-center mb-3">
          <span className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
            Overall Progress
          </span>
          <span
            className={cn(
              "text-2xl font-black tabular-nums",
              job.status === "DONE" && "text-emerald-400",
              job.status === "ERROR" && "text-red-400",
              job.status === "CANCELLED" && "text-orange-400",
              job.status === "RETRYING" && "text-amber-400",
              !["DONE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "text-foreground"
            )}
          >
            {job.progress}%
          </span>
        </div>

        {/* Gradient progress bar */}
        <div className="relative h-3 rounded-full overflow-hidden bg-muted/30 border border-border/20">
          <div
            className={cn(
              "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
              job.status === "DONE" && "bg-gradient-to-r from-emerald-500 via-emerald-400 to-emerald-300 shadow-[0_0_20px_rgba(16,185,129,0.3)]",
              job.status === "ERROR" && "bg-gradient-to-r from-red-600 via-red-500 to-red-400",
              job.status === "CANCELLED" && "bg-gradient-to-r from-orange-600 via-orange-500 to-orange-400",
              job.status === "RETRYING" && "bg-gradient-to-r from-amber-600 via-amber-500 to-amber-400",
              !["DONE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "bg-gradient-to-r from-violet-600 via-violet-500 to-fuchsia-500 shadow-[0_0_20px_rgba(139,92,246,0.25)]"
            )}
            style={{ width: `${Math.min(job.progress, 100)}%` }}
          />
        </div>

        {/* Action buttons */}
        <div className="flex justify-end mt-3 gap-2">
          {!["DONE", "ERROR", "CANCELLED"].includes(job.status) && onCancel && (
            <button
              onClick={() => onCancel(job.job_id)}
              className="px-4 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 rounded-lg font-bold text-[10px] uppercase tracking-wider transition-all active:scale-[0.97]"
            >
              Cancel Scan
            </button>
          )}
        </div>
      </div>

      {/* ── Status Banners ── */}
      {job.status === "RETRYING" && (
        <div className="p-5 rounded-2xl flex items-center gap-4 border border-amber-500/20 bg-amber-500/5 text-amber-400 animate-in fade-in duration-300">
          <div className="w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/25 flex items-center justify-center shrink-0">
            <RotateCw className="w-5 h-5 animate-spin" />
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-xs font-black uppercase tracking-tight">Retry in Progress</h4>
            <p className="text-[10px] opacity-70 mt-0.5 font-mono truncate">{job.stepMessage}</p>
          </div>
        </div>
      )}

      {job.status === "CANCELLED" && (
        <div className="p-5 rounded-2xl flex items-center gap-4 border border-orange-500/20 bg-orange-500/5 text-orange-400 animate-in fade-in duration-300">
          <div className="w-10 h-10 rounded-xl bg-orange-500/15 border border-orange-500/25 flex items-center justify-center shrink-0">
            <XCircle className="w-5 h-5" />
          </div>
          <div>
            <h4 className="text-xs font-black uppercase tracking-tight">Scan Cancelled</h4>
            <p className="text-[10px] opacity-70 mt-0.5">This job was cancelled by the user and sandbox resources were reclaimed.</p>
          </div>
        </div>
      )}

      {/* ── Render detailed report on-demand ── */}
      {job.status === "DONE" && result && (
        <div className="pt-2 animate-in fade-in zoom-in-95 duration-500">
          {onResultRender(result)}
        </div>
      )}
    </div>
  );
}
