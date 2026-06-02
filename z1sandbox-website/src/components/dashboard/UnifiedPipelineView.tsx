import React from "react";
import { CheckCircle2, AlertCircle, Loader2, Circle } from "lucide-react";
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
}

export function UnifiedPipelineView({
  job,
  steps,
  result,
  onResultRender,
}: UnifiedPipelineViewProps) {
  const currentStep = job.status;
  const currentIdx = steps.findIndex((s) => s.key === currentStep);

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Pipeline Step Indicators */}
      <div className="flex flex-col gap-3.5 bg-muted/10 p-5 rounded-2xl border border-border/50">
        <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-1">
          Scan Pipeline Execution
        </label>

        {steps.map((step, idx) => {
          // Status evaluation for this specific step
          const isDone = currentStep === "DONE" || (currentIdx !== -1 && idx < currentIdx);
          const isActive = step.key === currentStep;
          const isFailedStep = currentStep === "ERROR" && idx === Math.max(0, currentIdx);
          const isPending = !isDone && !isActive && !isFailedStep;

          return (
            <div
              key={step.key}
              className={cn(
                "flex items-center gap-3.5 py-2 px-3.5 rounded-xl border border-transparent transition-all duration-300",
                isActive && "bg-violet-500/10 border-violet-500/15 shadow-sm shadow-violet-500/5",
                isFailedStep && "bg-destructive/10 border-destructive/15",
                isDone && "bg-emerald-500/5 border-emerald-500/10"
              )}
            >
              {/* Animated Icon Indicator */}
              <div className="shrink-0">
                {isDone ? (
                  <div className="w-5 h-5 rounded-full flex items-center justify-center bg-emerald-500/15 border border-emerald-500/30 text-emerald-500">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  </div>
                ) : isFailedStep ? (
                  <div className="w-5 h-5 rounded-full flex items-center justify-center bg-destructive/15 border border-destructive/30 text-destructive animate-bounce">
                    <AlertCircle className="w-3.5 h-3.5" />
                  </div>
                ) : isActive ? (
                  <div className="w-5 h-5 rounded-full flex items-center justify-center bg-violet-500/15 border border-violet-500/30 text-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.3)]">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  </div>
                ) : (
                  <div className="w-5 h-5 rounded-full flex items-center justify-center border border-border text-muted-foreground/30">
                    <Circle className="w-2.5 h-2.5 fill-current" />
                  </div>
                )}
              </div>

              {/* Step Text Label */}
              <div className="flex-1 flex flex-col min-w-0">
                <span
                  className={cn(
                    "text-xs font-bold transition-colors",
                    isDone && "text-emerald-500",
                    isActive && "text-foreground",
                    isFailedStep && "text-destructive",
                    isPending && "text-muted-foreground/40"
                  )}
                >
                  {step.label}
                </span>

                {/* Active message details */}
                {isActive && job.stepMessage && (
                  <span className="text-[10px] text-muted-foreground/80 truncate mt-0.5 animate-pulse font-mono">
                    {job.stepMessage}
                  </span>
                )}
                {isFailedStep && job.stepMessage && (
                  <span className="text-[10px] text-destructive/80 font-mono mt-0.5">
                    {job.stepMessage}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Main Progress Indicator */}
      <div className="space-y-2">
        <div className="flex justify-between items-center text-[10px] font-black uppercase tracking-[0.15em] text-muted-foreground">
          <span>Overall Progress</span>
          <span className={cn(
            job.status === "DONE" && "text-emerald-500",
            job.status === "ERROR" && "text-destructive"
          )}>
            {job.progress}%
          </span>
        </div>
        <Progress
          value={job.progress}
          className={cn(
            "h-2 rounded-full overflow-hidden transition-all duration-500",
            job.status === "ERROR" && "bg-destructive/10"
          )}
        />
      </div>

      {/* Render detailed report on-demand */}
      {job.status === "DONE" && result && (
        <div className="pt-2 animate-in fade-in zoom-in-95 duration-500">
          {onResultRender(result)}
        </div>
      )}
    </div>
  );
}
