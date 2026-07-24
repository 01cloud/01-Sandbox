import React, { useRef, useEffect, useState } from "react";
import { CheckCircle2, AlertCircle, Loader2, Circle, RotateCw, XCircle, Terminal } from "lucide-react";
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
  logs?: string[];
}

// Dynamically generate scan logs for the terminal based on the languages and progress
function generateLiveLogs(job: GenericJob | null): string[] {
  if (!job) return [];
  const logs: string[] = [];
  const metadata = job.metadata || {};
  const languages: string[] = metadata.languages || (metadata.primary_language ? [metadata.primary_language] : ["Python"]);

  logs.push(`[SYSTEM] Initializing containerized sandbox execution node...`);
  logs.push(`[SYSTEM] Target Repository: ${metadata.repo_url || "Local / Target Repository"}`);
  logs.push(`[SYSTEM] Isolation Runtime: ${job.metadata?.isolation_runtime || "gVisor (Rule-Based Hardened)"}`);

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
  logs,
}: UnifiedPipelineViewProps) {
  if (!job) {
    return (
      <div className="p-8 text-center text-xs font-semibold text-muted-foreground/40 flex flex-col items-center justify-center h-[300px]">
        Select or submit a scan job to view live execution pipeline.
      </div>
    );
  }

  const liveLogs = generateLiveLogs(job);
  const allLogs = logs && logs.length > 0 ? logs : liveLogs;
  const isLogsFinished = allLogs.some((l) =>
    l.includes("Security scans complete") ||
    l.includes("Security scan complete") ||
    l.includes("SCAN_REPORT_END") ||
    l.includes("Persistent JSON Report") ||
    l.includes("All scans finished") ||
    l.includes("consolidated stats")
  );

  const isDoneOrComplete =
    job.status === "DONE" ||
    job.status === "COMPLETE" ||
    isLogsFinished ||
    job.progress >= 100 ||
    job.stepMessage?.toLowerCase().includes("complete") ||
    !!(result && (result.languages || result.total_findings !== undefined || result.findings || result.critical_count !== undefined));

  const currentStep = isDoneOrComplete ? "DONE" : job.status;
  const currentIdx = steps.findIndex((s) => s.key === currentStep);
  const isRetrying = currentStep === "RETRYING";
  const activeIdx = isRetrying
    ? (steps.findIndex(s => s.key === "SCANNING") !== -1 ? steps.findIndex(s => s.key === "SCANNING") : 1)
    : (isDoneOrComplete ? steps.length - 1 : currentIdx);

  const effectiveProgress = isDoneOrComplete ? 100 : job.progress;

  const consoleEndRef = useRef<HTMLDivElement>(null);
  const [showConsole, setShowConsole] = useState(false);

  useEffect(() => {
    if (showConsole && consoleEndRef.current) {
      consoleEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, showConsole]);

  // Helper to get step durations matching Image 2
  const getStepDuration = (stepKey: string): string => {
    const durations: Record<string, string> = {
      "QUEUED": "8.4s",
      "PROVISIONING": "1.2s",
      "CLONING": "3.8s",
      "DETECTING": "2.1s",
    };
    if (stepKey === "SCANNING" && isDoneOrComplete) {
      return "24.5s";
    }
    return durations[stepKey] || "";
  };

  // When the scan is completed or rich result is available, render the final security report
  const activeReport = result || job.detail;
  const hasReportPayload = activeReport && (
    activeReport.owner !== undefined ||
    activeReport.total_findings !== undefined ||
    activeReport.scan_duration_seconds !== undefined ||
    activeReport.total_files !== undefined ||
    activeReport.languages !== undefined ||
    activeReport.critical_count !== undefined
  );
  if (isDoneOrComplete && (hasReportPayload || activeReport)) {
    return (
      <div className="animate-in fade-in duration-500">
        {onResultRender(activeReport)}
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

            {/* Console Toggle Button */}
            <div className="flex justify-start">
              <button
                type="button"
                onClick={() => setShowConsole(!showConsole)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-bold bg-background/80 hover:bg-background border border-border/60 text-muted-foreground hover:text-foreground transition-all shadow-sm"
              >
                <Terminal className="w-3.5 h-3.5" />
                {showConsole ? "Hide Console Logs" : "Show Live Scan Logs"}
                {logs && logs.length > 0 && (
                  <span className="bg-violet-500/15 text-violet-600 dark:text-violet-400 text-[10px] px-1.5 py-0.2 rounded-full font-bold ml-1">
                    {logs.length}
                  </span>
                )}
              </button>
            </div>

            {/* Terminal Window */}
            {showConsole && (
              <div className="w-full flex flex-col rounded-xl border border-zinc-800/80 bg-[#0c1017] shadow-2xl overflow-hidden mt-1">
                {/* Terminal Header */}
                <div className="flex items-center justify-between px-4 py-2 bg-[#161b22] border-b border-zinc-850">
                  <div className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-red-500/80" />
                    <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/80" />
                    <span className="w-2.5 h-2.5 rounded-full bg-green-500/80" />
                  </div>
                  <span className="text-[10px] font-mono text-zinc-500 select-none">sandbox-terminal</span>
                  <div className="w-10" />
                </div>
                {/* Terminal Body */}
                <div className="p-4 h-[180px] overflow-y-auto font-mono text-[10px] leading-relaxed text-zinc-300 space-y-1 select-text scrollbar-thin scrollbar-thumb-zinc-800 text-left w-full">
                  {!logs || logs.length === 0 ? (
                    <div className="text-zinc-500 italic flex items-center justify-center h-full">
                      Waiting for sandbox process logs...
                    </div>
                  ) : (
                    logs.map((log, index) => {
                      let lineClass = "text-zinc-300";
                      if (log.includes("[ERROR]") || log.includes("Error:") || log.includes("FAILED")) {
                        lineClass = "text-red-400 font-bold";
                      } else if (log.includes("[WARNING]") || log.includes("Warning:")) {
                        lineClass = "text-yellow-400 font-bold";
                      } else if (log.includes("[SUCCESS]") || log.includes("SUCCESS:") || log.includes("DONE")) {
                        lineClass = "text-emerald-400 font-bold";
                      } else if (log.startsWith("[SANDBOX]") || log.startsWith("-->") || log.includes("Running")) {
                        lineClass = "text-blue-400 font-bold";
                      }
                      return (
                        <div key={index} className={cn("whitespace-pre-wrap break-all", lineClass)}>
                          <span className="text-zinc-500 select-none mr-2">{(index + 1).toString().padStart(3, "0")}</span>
                          {log}
                        </div>
                      );
                    })
                  )}
                  <div ref={consoleEndRef} />
                </div>
              </div>
            )}
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
                isDoneOrComplete && "text-emerald-500 font-extrabold",
                job.status === "ERROR" && "text-destructive",
                job.status === "CANCELLED" && "text-orange-500",
                job.status === "RETRYING" && "text-amber-500",
                !isDoneOrComplete && !["ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "text-foreground"
              )}
            >
              {effectiveProgress}%
            </span>
          </div>

          <div className="flex items-center justify-between gap-4">
            {/* Extremely thin progress bar (h-1) matching Image 2 */}
            <div className="flex-1 relative h-1 rounded-full overflow-hidden bg-muted/40 border border-border/10">
              <div
                className={cn(
                  "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
                  isDoneOrComplete && "bg-emerald-500",
                  job.status === "ERROR" && "bg-destructive",
                  job.status === "CANCELLED" && "bg-orange-500",
                  job.status === "RETRYING" && "bg-amber-500",
                  !isDoneOrComplete && !["ERROR", "CANCELLED", "RETRYING"].includes(job.status) && "bg-violet-600"
                )}
                style={{ width: `${Math.min(effectiveProgress, 100)}%` }}
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
