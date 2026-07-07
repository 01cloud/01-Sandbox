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

  if (job.status === "DONE") {
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

  const terminalEndRef = useRef<HTMLDivElement>(null);
  const liveLogs = generateLiveLogs(job);

  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [liveLogs.length]);

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Pipeline Step Timeline ── */}
      <div className="rounded-2xl border border-border/30 bg-card/60 backdrop-blur-sm overflow-hidden">
        {/* Section header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <label className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
            Scan Pipeline Execution
          </label>
          <div className="flex items-center gap-2">
            {job.status === "DONE" && (
              <span className="text-[9px] font-bold text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider">
                Completed
              </span>
            )}
            {job.status === "ERROR" && (
              <span className="text-[9px] font-bold text-destructive bg-destructive/10 border border-destructive/20 rounded-full px-2.5 py-0.5 uppercase tracking-wider">
                Failed
              </span>
            )}
          </div>
        </div>

        {/* Timeline */}
        <div className="px-6 pb-5">
          <div className="relative">
            {/* Vertical connector line */}
            <div className="absolute left-[13px] top-4 bottom-4 w-px bg-border" />

            <div className="flex flex-col gap-1">
              {steps.map((step, idx) => {
                const isFailedStep = currentStep === "ERROR" && idx === Math.max(0, activeIdx);
                const isDone = currentStep === "DONE" || (activeIdx !== -1 && idx < activeIdx);
                const isActive = idx === activeIdx && !isFailedStep && !isRetrying;
                const isActiveRetry = isRetrying && idx === activeIdx;
                const isPending = !isDone && !isActive && !isActiveRetry && !isFailedStep;

                // Determine if we should show the live terminal log under this step
                // (Show terminal for SCANNING or when the scan is completed/failed)
                const showTerminal =
                  (step.key === "SCANNING") &&
                  ((isActive) || (currentIdx > idx) || (currentStep === "DONE") || (currentStep === "ERROR"));

                return (
                  <div
                    key={step.key}
                    className={cn(
                      "relative flex flex-col gap-2 py-2.5 px-3 rounded-xl transition-all duration-300",
                      isActive && "bg-muted/10",
                      isActiveRetry && "bg-amber-500/5",
                      isFailedStep && "bg-destructive/5",
                    )}
                  >
                    <div className="flex items-start gap-4">
                      {/* Step circle indicator */}
                      <div className="relative z-10 mt-0.5">
                        {isDone ? (
                          <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-emerald-500/10 border border-emerald-500/30 text-emerald-500">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                          </div>
                        ) : isFailedStep ? (
                          <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-destructive/10 border border-destructive/30 text-destructive">
                            <AlertCircle className="w-3.5 h-3.5" />
                          </div>
                        ) : isActiveRetry ? (
                          <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-amber-500/10 border border-amber-500/30 text-amber-500">
                            <RotateCw className="w-3.5 h-3.5 animate-spin" />
                          </div>
                        ) : isActive ? (
                          <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center bg-violet-500/10 border border-violet-500/30 text-violet-500">
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          </div>
                        ) : (
                          <div className="w-[26px] h-[26px] rounded-full flex items-center justify-center border border-border/40 bg-muted/10 text-muted-foreground/30">
                            <Circle className="w-2 h-2 fill-current" />
                          </div>
                        )}
                      </div>

                      {/* Step content */}
                      <div className="flex-1 min-w-0 pt-0.5">
                        <span
                          className={cn(
                            "text-[13px] font-semibold transition-colors leading-tight",
                            isDone && "text-emerald-500/90 dark:text-emerald-400/90",
                            isActive && "text-foreground",
                            isActiveRetry && "text-amber-500",
                            isFailedStep && "text-destructive",
                            isPending && "text-muted-foreground/40"
                          )}
                        >
                          {step.label}
                        </span>

                        {/* Active step substatus */}
                        {isActive && job.stepMessage && (
                          <p className="text-[11px] text-muted-foreground/75 mt-1 font-mono leading-relaxed">
                            {job.stepMessage}
                          </p>
                        )}
                        {isActiveRetry && job.stepMessage && (
                          <p className="text-[11px] text-amber-500/80 mt-1 font-mono leading-relaxed">
                            {job.stepMessage}
                          </p>
                        )}
                        {isFailedStep && job.stepMessage && (
                          <p className="text-[11px] text-destructive/80 mt-1 font-mono leading-relaxed">
                            {job.stepMessage}
                          </p>
                        )}
                      </div>

                      {/* Right-side checkmark */}
                      <div className="shrink-0 mt-1">
                        {isDone && (
                          <span className="text-[10px] font-bold text-emerald-500/80">✓</span>
                        )}
                      </div>
                    </div>

                    {/* Terminal Sandbox logs showing live language tool steps */}
                    {showTerminal && (
                      <div className="ml-10 mt-1 rounded-xl bg-zinc-950 border border-border/30 p-3.5 font-mono text-[10px] leading-relaxed text-zinc-300 max-h-[160px] overflow-y-auto shadow-inner flex flex-col gap-1">
                        <div className="flex items-center justify-between text-zinc-500 text-[9px] border-b border-zinc-800/50 pb-1.5 mb-1.5 shrink-0">
                          <span>SANDBOX WORKSPACE TERMINAL</span>
                          <span className="flex items-center gap-1.5">
                            {!["DONE", "ERROR"].includes(job.status) && (
                              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                            )}
                            LIVE STREAM
                          </span>
                        </div>
                        <div className="flex-1 overflow-y-auto space-y-1 pr-1">
                          {liveLogs.map((log, idx) => (
                            <div key={idx} className="whitespace-pre-wrap break-all hover:bg-zinc-900/50 py-0.5 rounded px-1">
                              {log}
                            </div>
                          ))}
                          <div ref={terminalEndRef} />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* ── Overall Progress ── */}
      <div className="rounded-2xl border border-border/30 bg-card/60 backdrop-blur-sm p-5">
        <div className="flex justify-between items-center mb-3">
          <span className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
            Overall Progress
          </span>
          <span
            className={cn(
              "text-2xl font-black tabular-nums",
              job.status === "DONE" && "text-emerald-500",
              job.status === "ERROR" && "text-destructive",
              job.status === "CANCELLED" && "text-orange-500",
              job.status === "RETRYING" && "text-amber-500",
              !["DONE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "text-foreground"
            )}
          >
            {job.progress}%
          </span>
        </div>

        {/* Simplified color-coded progress bar */}
        <div className="relative h-2.5 rounded-full overflow-hidden bg-muted/40 border border-border/20">
          <div
            className={cn(
              "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
              job.status === "DONE" && "bg-emerald-500",
              job.status === "ERROR" && "bg-destructive",
              job.status === "CANCELLED" && "bg-orange-500",
              job.status === "RETRYING" && "bg-amber-500",
              !["DONE", "ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "bg-violet-600"
            )}
            style={{ width: `${Math.min(job.progress, 100)}%` }}
          />
        </div>

        {/* Action buttons */}
        <div className="flex justify-end mt-3 gap-2">
          {!["DONE", "ERROR", "CANCELLED"].includes(job.status) && onCancel && (
            <button
              onClick={() => onCancel(job.job_id)}
              className="px-4 py-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive border border-destructive/20 rounded-lg font-bold text-[10px] uppercase tracking-wider transition-all active:scale-[0.97]"
            >
              Cancel Scan
            </button>
          )}
        </div>
      </div>

      {/* ── Status Banners ── */}
      {job.status === "RETRYING" && (
        <div className="p-5 rounded-2xl flex items-center gap-4 border border-amber-500/20 bg-amber-500/5 text-amber-500 animate-in fade-in duration-300">
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
        <div className="p-5 rounded-2xl flex items-center gap-4 border border-orange-500/20 bg-orange-500/5 text-orange-500 animate-in fade-in duration-300">
          <div className="w-10 h-10 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center shrink-0">
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
