import { useState, useEffect } from "react";
import {
  X,
  ShieldCheck,
  Zap,
  AlertCircle,
  FileCode,
  Layout,
  Terminal,
  Activity,
  Plus
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useJobStore } from "@/hooks/useJobStore";
import { JobsPanel } from "./JobsPanel";
import { UnifiedPipelineView } from "./UnifiedPipelineView";

interface SecurityScannerProps {
  isOpen: boolean;
  onClose: () => void;
  backend: string;
  baseUrl: string;
  apiKey: string;
}

const QUICK_SCAN_STEPS = [
  { key: "QUEUED", label: "Job Queued" },
  { key: "PROVISIONING", label: "Provision Sandbox & Ingest" },
  { key: "SCANNING", label: "Security Analysis" },
  { key: "DONE", label: "Scan Completed" },
];

const SecurityScanner = ({ isOpen, onClose, backend, baseUrl, apiKey }: SecurityScannerProps) => {
  const [code, setCode] = useState("# Simple Code Example\ndef greet(name):\n    return f\"Hello, {name}!\"\n\nprint(greet(\"User\"))");
  const [isScanning, setIsScanning] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  // Initialize unified hook
  const {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
  } = useJobStore("quick-scan", baseUrl, apiKey);

  // Auto-select latest job if any
  useEffect(() => {
    if (jobs.length > 0 && !selectedJobId) {
      setSelectedJobId(jobs[0].job_id);
    }
  }, [jobs, selectedJobId]);

  // Lazy load PVC report when selecting a completed job
  useEffect(() => {
    if (selectedJobId) {
      const job = jobs.find((j) => j.job_id === selectedJobId);
      if (job && job.status === "DONE" && !volatileResults[selectedJobId]) {
        lazyFetchResult(selectedJobId);
      }
    }
  }, [selectedJobId, jobs, volatileResults]);

  const detectLanguage = (code: string) => {
    const text = code.trim();
    if (!text) return "py";

    const scores: Record<string, number> = {
      py: 0,
      yaml: 0,
      k8s: 0,
      js: 0,
      go: 0,
      sh: 0
    };

    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
      try {
        JSON.parse(text);
        return "json";
      } catch (e) { /* ignore */ }
    }

    if (text.startsWith("#!")) return "sh";

    if (/\b(import|from)\s+\w+/.test(text)) scores.py += 10;
    if (/\bdef\s+\w+\(/.test(text)) scores.py += 10;
    if (/\bclass\s+\w+[:\(]/.test(text)) scores.py += 10;
    if (/\bprint\(/.test(text)) scores.py += 5;
    if (/\bif\s+__name__\s*==/.test(text)) scores.py += 20;

    if (text.startsWith("---")) scores.yaml += 15;
    const hasApiVersion = /apiVersion:/m.test(text);
    const hasKind = /kind:/m.test(text);
    if (hasApiVersion && hasKind) {
      scores.k8s += 30;
    } else if (hasApiVersion || hasKind || /^(metadata|spec|services|version):/m.test(text)) {
      scores.yaml += 10;
    }

    if (/\b(const|let|var)\s+\w+\s*=/.test(text)) scores.js += 5;
    if (/\bimport\s+.*from\s+['"]/.test(text)) scores.js += 10;
    if (/\bconsole\.log\(/.test(text)) scores.js += 5;

    if (/\bpackage\s+\w+/.test(text)) scores.go += 15;
    if (/\bfunc\s+\w+\(/.test(text)) scores.go += 10;

    if (/\b(sudo|apt-get|yum|export|grep|awk|sed)\b/.test(text)) scores.sh += 5;

    let maxScore = -1;
    let detected = "py";
    for (const lang in scores) {
      if (scores[lang] > maxScore) {
        maxScore = scores[lang];
        detected = lang;
      }
    }
    return maxScore > 0 ? detected : "py";
  };

  const runScan = async () => {
    if (!code.trim()) {
      toast.error("Please provide code to scan");
      return;
    }

    if (!apiKey) {
      toast.error(`No API Key found for ${backend}. Please create one in the API Management tab.`);
      return;
    }

    try {
      setIsScanning(true);

      const lang = detectLanguage(code);
      const apiExt = lang === 'k8s' ? 'yaml' : lang;
      const filename = `input.${apiExt}`;

      // POST asynchronously to support SSE streams
      const response = await fetch(`${baseUrl}/scan-jobs?async=true`, {
        method: "POST",
        headers: {
          "accept": "application/json",
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          files: { [filename]: code }
        })
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "Backend returned an error");

      const { job_id } = data;

      // Add to store
      addJob({
        job_id,
        job_type: "quick-scan",
        status: "QUEUED",
        progress: 5,
        stepMessage: "Job queued",
        eventIndex: 0,
        metadata: {
          files_count: 1,
          submitted_at: new Date().toISOString()
        },
        summary: null,
        result: null,
        submittedAt: new Date().toISOString(),
        completedAt: null
      });

      setSelectedJobId(job_id);
      openStream(job_id, 0);

      toast.success("Security audit pipeline initiated!");
    } catch (error: any) {
      console.error("Scan error:", error);
      toast.error(error.message || "Audit failed to initiate");
    } finally {
      setIsScanning(false);
    }
  };

  // Find currently selected job record
  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || null;
  const selectedResult = selectedJobId ? volatileResults[selectedJobId] : null;

  // Custom renderer for scan result findings
  const renderQuickScanResult = (result: any) => {
    const normalized = result.report || result;
    const findings = normalized.findings || [];
    const totalFindings = findings.length;

    return (
      <div className="space-y-6 mt-4">
        {/* Verdict Banner */}
        <div
          className={cn(
            "p-5 rounded-2xl flex items-center justify-between border shadow-sm",
            totalFindings === 0
              ? "bg-emerald-500/5 border-emerald-500/20 text-emerald-400"
              : "bg-destructive/5 border-destructive/20 text-destructive"
          )}
        >
          <div className="flex flex-col">
            <span className="text-[9px] font-black uppercase tracking-[0.2em] opacity-60">Audit Verdict</span>
            <div className="flex items-center gap-2 mt-1">
              <h2 className="text-xl font-black tracking-tight uppercase">
                {totalFindings === 0 ? "SECURE" : "VULNERABILITIES DETECTED"}
              </h2>
              <Badge variant="outline" className="border-current/30 text-[8px] uppercase font-black py-0 h-4">
                {totalFindings} RISKS
              </Badge>
            </div>
          </div>
        </div>

        {/* Developer split view: Report vs Insights */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 h-[calc(100vh-320px)] min-h-[400px]">
          {/* JSON Telemetry */}
          <section className="flex flex-col gap-3 overflow-hidden">
            <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">
              Security Telemetry Report
            </h3>
            <div className="flex-1 bg-zinc-950 rounded-2xl border border-border/50 shadow-2xl overflow-hidden">
              <ScrollArea className="h-full">
                <pre className="p-6 text-[11px] font-mono text-emerald-500/80 leading-relaxed whitespace-pre font-medium">
                  {JSON.stringify(normalized, null, 2)}
                </pre>
              </ScrollArea>
            </div>
          </section>

          {/* Vulnerability Breakdown */}
          <div className="flex flex-col gap-6 overflow-hidden">
            <section className="flex flex-col gap-3 h-full overflow-hidden">
              <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground shrink-0">
                Vulnerability Insights
              </h3>
              <ScrollArea className="flex-1 border border-border/50 rounded-2xl p-4 bg-muted/5">
                {findings.length > 0 ? (
                  <div className="space-y-4">
                    {findings.map((f: any, i: number) => {
                      const severity = (f.severity || "MEDIUM").toUpperCase();
                      const sevColor =
                        severity === "CRITICAL"
                          ? "bg-red-500/10 text-red-500 border-red-500/20"
                          : severity === "HIGH"
                          ? "bg-orange-500/10 text-orange-500 border-orange-500/20"
                          : severity === "MEDIUM"
                          ? "bg-amber-500/10 text-amber-500 border-amber-500/20"
                          : "bg-blue-500/10 text-blue-500 border-blue-500/20";

                      return (
                        <div key={i} className="p-4 rounded-xl border bg-card hover:bg-muted/15 transition-all relative overflow-hidden">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className={cn("text-[8px] font-black px-1.5 py-0", sevColor)}>
                                {severity}
                              </Badge>
                              <span className="text-[9px] font-mono text-muted-foreground tracking-widest uppercase">
                                {f.tool}
                              </span>
                            </div>
                            {f.line && <span className="text-[9px] font-mono opacity-40">L:{f.line}</span>}
                          </div>

                          <h4 className="text-xs font-bold mb-2 text-foreground">
                            {f.issue || "Security violation"}
                          </h4>

                          <div className="flex flex-col gap-1.5 mt-3">
                            <span className="text-[8px] font-black tracking-[0.1em] text-muted-foreground/60 uppercase">
                              remediation insight
                            </span>
                            <div className="p-3 rounded-lg bg-muted text-[10px] text-muted-foreground leading-relaxed italic border border-border/30">
                              {f.remediation || "Analyze the specific code structure and apply industry security standards to mitigate this risk."}
                            </div>
                          </div>

                          <div className="mt-2 text-[8px] font-mono opacity-40">
                            FILE: {f.file || "unknown"}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center opacity-30 py-20">
                    <span className="text-[10px] font-black uppercase tracking-widest">Integrity Verified</span>
                  </div>
                )}
              </ScrollArea>
            </section>
          </div>
        </div>
      </div>
    );
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[100vw] w-screen h-screen m-0 p-0 overflow-hidden border-none bg-background flex flex-col rounded-none">

        {/* Top Header */}
        <DialogHeader className="px-8 py-5 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
          <div className="flex items-center gap-4">
            <div>
              <DialogTitle className="text-lg font-black tracking-tight uppercase leading-none">
                Security Intelligence Operations
              </DialogTitle>
              <DialogDescription className="text-[10px] font-bold text-muted-foreground uppercase tracking-[0.3em] flex items-center gap-2 mt-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] animate-pulse" />
                Cluster Node: {backend}
              </DialogDescription>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl hover:bg-muted/50 transition-colors text-muted-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </DialogHeader>

        {/* Triple Panel Layout */}
        <div className="flex-1 flex overflow-hidden">

          {/* Panel 1: Code input (left) */}
          <div className="w-[450px] flex flex-col p-6 bg-muted/5 border-r border-border/50 shrink-0 gap-4">
            <div className="flex items-center justify-between text-muted-foreground">
              <label className="text-[10px] font-black uppercase tracking-[0.2em]">Source Ingestion</label>
              <Badge variant="outline" className="rounded-md font-mono text-[9px] font-bold px-2 py-0">
                {detectLanguage(code).toUpperCase()}
              </Badge>
            </div>

            <div className="flex-1 relative group rounded-2xl bg-card border border-border/50 overflow-hidden focus-within:ring-2 focus-within:ring-violet-500/25 transition-all">
              <textarea
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="w-full h-full bg-transparent p-5 font-mono text-xs focus:outline-none resize-none leading-relaxed"
                spellCheck="false"
                placeholder="# Paste code..."
              />
            </div>

            <Button
              onClick={runScan}
              disabled={isScanning}
              className="h-11 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-bold text-xs uppercase tracking-widest transition-all shadow-lg shadow-violet-600/15 flex items-center justify-center gap-2 active:scale-[0.98]"
            >
              {isScanning ? (
                <>
                  <LoadingSpinner size="sm" className="text-current" />
                  <span>Ingesting...</span>
                </>
              ) : (
                <>
                  <Zap className="w-3.5 h-3.5 fill-current" />
                  EXECUTE AUDIT
                </>
              )}
            </Button>
          </div>

          {/* Panel 2: Sidebar list panel (middle) */}
          <JobsPanel
            jobs={jobs}
            selectedJobId={selectedJobId}
            onSelectJob={setSelectedJobId}
            onDeleteJob={removeJob}
            jobType="quick-scan"
          />

          {/* Panel 3: Execution View / Result renderer (right) */}
          <div className="flex-1 bg-background overflow-hidden flex flex-col p-6">
            <ScrollArea className="flex-1">
              <div className="max-w-5xl mx-auto w-full">
                {selectedJob ? (
                  <UnifiedPipelineView
                    job={selectedJob}
                    steps={QUICK_SCAN_STEPS}
                    result={selectedResult}
                    onResultRender={renderQuickScanResult}
                  />
                ) : (
                  <div className="h-[60vh] flex flex-col items-center justify-center text-center gap-4 opacity-40">
                    <Activity className="w-12 h-12 text-muted-foreground animate-pulse" />
                    <div>
                      <h3 className="text-sm font-black uppercase tracking-wider">No Scan Selected</h3>
                      <p className="text-xs text-muted-foreground mt-1">
                        Select a job from the panel or execute a new code audit.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
          </div>

        </div>
      </DialogContent>
    </Dialog>
  );
};

export default SecurityScanner;
