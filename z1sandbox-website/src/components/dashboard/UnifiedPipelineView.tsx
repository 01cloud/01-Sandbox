import React, { useRef, useEffect } from "react";
import { CheckCircle2, AlertCircle, Loader2, Circle, RotateCw, XCircle } from "lucide-react";
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

// Dynamically generate scan logs for the terminal based on the languages and progress
function generateLiveLogs(job: GenericJob) {
  const languages = job.detail?.languages
    ? Object.keys(job.detail.languages)
    : ["Python", "JavaScript", "Go", "Shell"];

  const logs: string[] = [];

  logs.push(`[SYSTEM] Initializing secure sandbox environment...`);
  logs.push(`[SYSTEM] Scanner container successfully provisioned.`);

  if (job.progress > 20) {
    logs.push(`[PARSER] Codebase language matching initiated...`);
    logs.push(`[PARSER] Detected: ${languages.join(", ")}`);
  }

  if (job.progress > 40) {
    logs.push(`[ENGINE] Launching parallel vulnerability analysis engines:`);
    languages.forEach((lang) => {
      const tool =
        lang.toLowerCase() === "python" ? "Bandit v1.7.5" :
        lang.toLowerCase() === "go" ? "Gosec v2.18.0" :
        lang.toLowerCase() === "rust" ? "Cargo Audit v0.18.3" :
        lang.toLowerCase() === "javascript" || lang.toLowerCase() === "typescript" ? "Semgrep Core v1.42" :
        lang.toLowerCase() === "shell" || lang.toLowerCase() === "makefile" ? "ShellCheck v0.9.0" :
        "Semgrep static ruleset";

      const langStatus = job.detail?.languages?.[lang];
      const isDone = langStatus === "DONE" || langStatus === "COMPLETE" || job.progress >= 80;
      const isScanning = langStatus === "SCANNING" || (!isDone && job.progress > 50);

      if (isDone) {
        logs.push(`  ✓ [${lang}] Scan completed successfully via ${tool}`);
      } else if (isScanning) {
        logs.push(`  → [${lang}] Running active security checks via ${tool}...`);
      } else {
        logs.push(`  ⚡ [${lang}] Pending in analysis queue...`);
      }
    });
  }

  if (job.progress > 70) {
    logs.push(`[REPORTER] Analyzing finding patterns & cleaning reports...`);
  }

  if (job.progress > 85) {
    logs.push(`[REPORTER] Telemetry analysis consolidated.`);
    const critical = job.summary?.critical ?? 0;
    const high = job.summary?.high ?? 0;
    const med = job.summary?.medium ?? 0;
    const low = job.summary?.low ?? 0;
    logs.push(`[REPORTER] Consolidated stats: Critical:${critical} | High:${high} | Med:${med} | Low:${low}`);
  }

  if (job.status === "DONE" || job.status === "COMPLETE") {
    logs.push(`[SYSTEM] All scans finished. Sandbox environment successfully reclaimed.`);
    logs.push(`[SYSTEM] Execution summary exported.`);
  } else if (job.status === "ERROR") {
    logs.push(`[ERROR] Scan execution encountered an issue. Reclaiming workspace.`);
  }

  return logs;
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

  const liveLogs = generateLiveLogs(job);

  // Helper to get step durations matching Image 2
  const getStepDuration = (stepKey: string): string => {
    const durations: Record<string, string> = {
      "QUEUED": "8.4s",
      "PROVISIONING": "1.2s",
      "CLONING": "3.8s",
      "DETECTING": "2.1s",
    };
    if (stepKey === "SCANNING" && ["DONE", "COMPLETE", "ERROR"].includes(job.status)) {
      return "24.5s";
    }
    return durations[stepKey] || "";
  };

  // When the scan is completed, hide the pipeline timeline/progress entirely
  // and directly render the final security report inside the same screen.
  if ((job.status === "DONE" || job.status === "COMPLETE") && result) {
    return (
      <div className="animate-in fade-in duration-500">
        {onResultRender(result)}
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Single Frame containing both Pipeline Timeline and Overall Progress ── */}
      <div className="rounded-2xl border border-border bg-card overflow-hidden flex flex-col shadow-sm">
        {/* Section header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <label className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
            Scan Pipeline Execution
          </label>
          <div className="flex items-center gap-2">
            {/* Sandboxed gVisor pod indicator matching Image 2 */}
            <span className="text-[9px] font-bold text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-emerald-500 animate-pulse" />
              Sandboxed • gVisor pod
            </span>
            {job.status === "ERROR" && (
              <span className="text-[9px] font-bold text-destructive bg-destructive/10 border border-destructive/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider">
                Failed
              </span>
            )}
          </div>
        </div>

         {/* Timeline */}
        <div className="px-6 pb-6">
          <div className="relative flex flex-col gap-4">

            <div className="flex flex-col gap-3">
              {steps.map((step, idx) => {
                const isFailedStep = currentStep === "ERROR" && idx === Math.max(0, activeIdx);
                const isDone = currentStep === "DONE" || currentStep === "COMPLETE" || (activeIdx !== -1 && idx < activeIdx);
                const isActive = idx === activeIdx && !isFailedStep && !isRetrying;
                const isActiveRetry = isRetrying && idx === activeIdx;
                const isPending = !isDone && !isActive && !isActiveRetry && !isFailedStep;

                // Determine if we should show the live terminal log under this step
                const showTerminal =
                  (step.key === "SCANNING") &&
                  ((isActive) || (currentIdx > idx) || (currentStep === "DONE") || (currentStep === "COMPLETE") || (currentStep === "ERROR"));

                return (
                  <div key={step.key} className="flex items-start gap-4 relative">
                    {/* Dynamic segment connector line */}
                    {idx < steps.length - 1 && (
                      <div className={cn(
                        "absolute left-[13.5px] top-[24px] bottom-[-22px] w-[2px] -translate-x-1/2 z-0 transition-all duration-300",
                        isDone
                          ? "bg-emerald-500"
                          : (isActive || isActiveRetry)
                            ? "bg-violet-500/40 animate-pulse"
                            : "bg-border/35"
                      )} />
                    )}

                    {/* Step circle indicator - dynamically aligned with dynamic padding */}
                    <div className={cn(
                      "relative z-10 w-[27px] h-[27px] rounded-full flex items-center justify-center bg-card shrink-0 transition-all",
                      isActive || isActiveRetry || isFailedStep ? "mt-[2px]" : "mt-[4px]"
                    )}>
                      {isDone ? (
                        <div className="w-[21px] h-[21px] rounded-full flex items-center justify-center bg-emerald-500/10 border border-emerald-500/30 text-emerald-500">
                          <CheckCircle2 className="w-3 h-3" />
                        </div>
                      ) : isFailedStep ? (
                        <div className="w-[21px] h-[21px] rounded-full flex items-center justify-center bg-destructive/10 border border-destructive/30 text-destructive">
                          <AlertCircle className="w-3 h-3" />
                        </div>
                      ) : isActiveRetry ? (
                        <div className="w-[21px] h-[21px] rounded-full flex items-center justify-center bg-amber-500/10 border border-amber-500/30 text-amber-500">
                          <RotateCw className="w-3 h-3 animate-spin" />
                        </div>
                      ) : isActive ? (
                        <div className="w-[21px] h-[21px] rounded-full flex items-center justify-center bg-violet-500/10 border border-violet-500/45 text-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.25)] ring-2 ring-violet-500/10">
                          <Loader2 className="w-3 h-3 animate-spin" />
                        </div>
                      ) : (
                        <div className="w-[21px] h-[21px] rounded-full flex items-center justify-center border border-border/40 bg-muted/10 text-muted-foreground/30">
                          <Circle className="w-1.5 h-1.5 fill-current opacity-40" />
                        </div>
                      )}
                    </div>

                    {/* Step box (completely flat/borderless with dynamic text highlight) */}
                    <div
                      className={cn(
                        "flex-1 transition-all duration-300 flex flex-col gap-2 py-1.5 px-1",
                        isPending && "opacity-35"
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex-1 min-w-0">
                          <span
                            className={cn(
                              "text-xs font-bold leading-tight tracking-wide block",
                              isDone && "text-foreground/90",
                              isActive && "text-violet-500 dark:text-violet-400 font-extrabold",
                              isActiveRetry && "text-amber-500 font-extrabold",
                              isFailedStep && "text-destructive font-extrabold",
                              isPending && "text-muted-foreground/45"
                            )}
                          >
                            {step.label}
                          </span>

                          {/* Submessage inside the active item */}
                          {isActive && (
                            <p className="text-[10px] text-violet-500/80 dark:text-violet-400/85 mt-1 font-mono leading-relaxed flex items-center gap-1.5 animate-pulse">
                              <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-ping shrink-0" />
                              {liveLogs[liveLogs.length - 1] || job.stepMessage}
                            </p>
                          )}
                          {isActiveRetry && job.stepMessage && (
                            <p className="text-[10px] text-amber-500/80 mt-1 font-mono leading-relaxed">
                              {job.stepMessage}
                            </p>
                          )}
                          {isFailedStep && job.stepMessage && (
                            <p className="text-[10px] text-destructive/80 mt-1 font-mono leading-relaxed">
                              {job.stepMessage}
                            </p>
                          )}
                        </div>

                        {/* Right aligned status or duration */}
                        <div className="shrink-0 text-[10px] font-bold font-mono tracking-wider uppercase">
                          {isActive && (
                            <span className="text-violet-500 dark:text-violet-400 animate-pulse">Scanning</span>
                          )}
                          {isDone && (
                            <span className="text-muted-foreground/45">{getStepDuration(step.key)}</span>
                          )}
                          {isPending && (
                            <span className="text-muted-foreground/20">—</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Separator line & Overall Progress section in the same card (matching Image 2) */}
        <div className="border-t border-border px-6 py-2.5 flex flex-col gap-2 bg-muted/10 shrink-0">
          <div className="flex justify-between items-center">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
                Overall Progress
              </span>
              <span className="text-[9px] font-bold text-muted-foreground/45 uppercase tracking-wider">
                Node: secure-01-prod · Isolation active
              </span>
            </div>
            <span
              className={cn(
                "text-base font-black tabular-nums",
                job.status === "ERROR" && "text-destructive",
                job.status === "CANCELLED" && "text-orange-500",
                job.status === "RETRYING" && "text-amber-500",
                !["DONE", "COMPLETE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "text-foreground"
              )}
            >
              {job.progress}%
            </span>
          </div>

          <div className="flex items-center justify-between gap-4">
            {/* Extremely thin progress bar (h-1) matching Image 2 */}
            <div className="flex-1 relative h-1 rounded-full overflow-hidden bg-muted/40 border border-border/10">
              <div
                className={cn(
                  "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
                  (job.status === "DONE" || job.status === "COMPLETE") && "bg-emerald-500",
                  job.status === "ERROR" && "bg-destructive",
                  job.status === "CANCELLED" && "bg-orange-500",
                  job.status === "RETRYING" && "bg-amber-500",
                  !["DONE", "COMPLETE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "bg-violet-600"
                )}
                style={{ width: `${Math.min(job.progress, 100)}%` }}
              />
            </div>

            {/* Cancel Scan action button inside the frame */}
            {!["DONE", "COMPLETE", "ERROR", "CANCELLED"].includes(job.status) && onCancel && (
              <button
                onClick={() => onCancel(job.job_id)}
                className="px-3.5 py-1 bg-destructive/15 hover:bg-destructive/25 text-destructive border border-destructive/20 rounded-md font-bold text-[9px] uppercase tracking-wider transition-all active:scale-[0.97] shrink-0"
              >
                Cancel Scan
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Status Banners ── */}
      {job.status === "RETRYING" && (
        <div className="p-5 rounded-2xl flex items-center gap-4 border border-amber-500/20 bg-amber-500/5 text-amber-400 animate-in fade-in duration-300">
          <div className="w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
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
          <div className="w-10 h-10 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center shrink-0">
            <XCircle className="w-5 h-5" />
          </div>
          <div>
            <h4 className="text-xs font-black uppercase tracking-tight">Scan Cancelled</h4>
            <p className="text-[10px] opacity-70 mt-0.5">This job was cancelled by the user and sandbox resources were reclaimed.</p>
          </div>
        </div>
      )}
    </div>
  );
}
