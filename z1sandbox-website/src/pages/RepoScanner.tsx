import { useState, useEffect, useRef } from "react";
import {
  Github, Search, CheckCircle2, AlertCircle, Loader2,
  BarChart3, ChevronDown, ChevronUp, ArrowLeft, FileCode, Shield
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useNavigate } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell
} from "recharts";

interface ScanEvent {
  job_id: string;
  step: string;
  message: string;
  progress: number;
  detail?: any;
}

interface FindingItem {
  severity: string;
  file: string;
  line?: number;
  issue: string;
  tool: string;
  remediation?: string;
}

interface LanguageResult {
  language: string;
  file_count: number;
  lines_of_code: number;
  percentage: number;
  findings: FindingItem[];
}

interface ScanResult {
  job_id: string;
  repo_url: string;
  owner: string;
  repo: string;
  status: string;
  languages: Record<string, LanguageResult>;
  detection_tool: string;
  total_files: number;
  total_findings: number;
  scan_duration_seconds: number;
  error?: string;
}

const GITHUB_PATTERN = /^https:\/\/github\.com\/[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+\/?$/;

const STEPS = ["QUEUED", "PROVISIONING", "CLONING", "DETECTING", "SCANNING", "DONE"];
const STEP_LABELS: Record<string, string> = {
  QUEUED: "Job Queued",
  PROVISIONING: "Provisioning Sandbox",
  CLONING: "Cloning Repository",
  DETECTING: "Detecting Languages",
  SCANNING: "Scanning Files",
  DONE: "Complete",
};

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

function getApiKey(): string | null {
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i) || "";
    if (key.startsWith("bound_key_")) {
      const val = localStorage.getItem(key);
      if (val) return val;
    }
  }
  return null;
}

function getApiBaseUrl(): string {
  return (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "";
}

export default function RepoScanner() {
  const navigate = useNavigate();
  const API_BASE = getApiBaseUrl();

  const [repoUrl, setRepoUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [currentStep, setCurrentStep] = useState("");
  const [stepMessage, setStepMessage] = useState("");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);
  // ID of the job currently streamed on this page (UI-submitted or CLI-detected)
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => { esRef.current?.close(); }, []);

  const validateUrl = (url: string) => {
    if (!url) { setUrlError(""); return; }
    setUrlError(GITHUB_PATTERN.test(url.trim()) ? "" : "Must be: https://github.com/owner/repo");
  };

  const resetScan = () => {
    setCurrentStep(""); setStepMessage(""); setProgress(0); setResult(null); setExpandedLang(null);
    setActiveJobId(null);
    esRef.current?.close(); esRef.current = null;
  };

  /** Connect an SSE stream for any job_id — shared by UI-submit and CLI-detect paths. */
  const connectStream = (job_id: string, apiKey: string, since = 0) => {
    esRef.current?.close();
    setActiveJobId(job_id);
    setIsScanning(true);

    const es = new EventSource(
      `${API_BASE}/v1/repo-scan/${job_id}/status?token=${encodeURIComponent(apiKey)}&since=${since}`
    );
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const ev: ScanEvent = JSON.parse(e.data);
        setCurrentStep(ev.step);
        setStepMessage(ev.message);
        setProgress(ev.progress);

        if (ev.step === "DONE") {
          if (ev.detail) setResult(ev.detail as ScanResult);
          setIsScanning(false); es.close();
        } else if (ev.step === "ERROR") {
          toast.error(ev.message);
          setIsScanning(false); es.close();
        }
      } catch { /* ignore */ }
    };

    es.onerror = () => {
      es.close();
      fetch(`${API_BASE}/v1/repo-scan/${job_id}/result`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setResult(d); setIsScanning(false); })
        .catch(() => setIsScanning(false));
    };
  };

  /**
   * Poll /v1/repo-scan/jobs every 5 seconds.
   * If an active job exists that we are not already streaming, auto-connect.
   * This surfaces CLI-triggered scans without requiring the user to submit via the UI.
   */
  useEffect(() => {
    const apiKey = getApiKey();
    if (!apiKey) return;

    const poll = async () => {
      // Don't interrupt an already-running stream
      if (esRef.current) return;
      try {
        const resp = await fetch(`${API_BASE}/v1/repo-scan/jobs`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        });
        if (!resp.ok) return;
        const serverJobs: Array<{ job_id: string; status: string; eventIndex: number }> =
          await resp.json();

        // Pick the first active job that isn't already displayed
        const active = serverJobs.find(
          j => !["DONE", "ERROR"].includes(j.status)
        );
        if (active && active.job_id !== activeJobId) {
          toast.info("CLI scan detected — connecting to live stream...");
          connectStream(active.job_id, apiKey, active.eventIndex ?? 0);
        }
      } catch { /* silent */ }
    };

    poll(); // immediate on mount
    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API_BASE, activeJobId]);

  const handleScan = async () => {
    const url = repoUrl.trim();
    if (!GITHUB_PATTERN.test(url)) { setUrlError("Enter a valid public GitHub URL"); return; }

    const apiKey = getApiKey();
    if (!apiKey) {
      toast.error("No API key found. Create one in the Dashboard → API Management tab.");
      return;
    }

    resetScan();
    setIsScanning(true);

    try {
      const resp = await fetch(`${API_BASE}/v1/repo-scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ repo_url: url }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start scan");

      connectStream(data.job_id, apiKey, 0);
    } catch (err: any) {
      toast.error(err.message);
      setIsScanning(false);
    }
  };

  const langEntries = result && result.languages ? Object.entries(result.languages) : [];
  const chartData = langEntries.map(([lang, r]) => ({
    name: lang,
    "%": r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
  }));
  const stepIdx = STEPS.indexOf(currentStep);

  return (
    <div className="min-h-screen pt-28 pb-20 px-6 sm:px-10 max-w-7xl mx-auto">
      <header className="mb-10">
        <button
          onClick={() => navigate("/dashboard")}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6 group"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
          Back to Dashboard
        </button>

        <div className="flex items-start gap-4">
          <div className="p-3 rounded-2xl bg-violet-500/10 border border-violet-500/20 text-violet-500 shrink-0 mt-1">
            <Github className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-4xl font-display font-black tracking-tight">GitHub Repository Scanner</h1>
            <p className="text-muted-foreground text-lg mt-2 max-w-2xl">
              Deep-scan any public GitHub repository — accurate language detection via{" "}
              <span className="text-violet-500 font-semibold">linguist → tokei → enry</span>, plus per-language
              static analysis.
            </p>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-8 items-start">
        {/* Left Panel */}
        <div className="sticky top-28 flex flex-col gap-6">
          <div className="rounded-[2rem] border border-border/50 bg-background/50 backdrop-blur-sm p-8 flex flex-col gap-5 shadow-xl shadow-primary/5">
            <div>
              <label htmlFor="repo-url-input" className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block mb-2">
                Repository URL
              </label>
              <Input
                id="repo-url-input"
                value={repoUrl}
                onChange={(e) => { setRepoUrl(e.target.value); validateUrl(e.target.value); }}
                onBlur={() => validateUrl(repoUrl)}
                placeholder="https://github.com/owner/repo"
                className={cn("rounded-xl h-12 font-mono text-sm border-2 transition-colors",
                  urlError ? "border-destructive" : "border-border/50 focus:border-violet-500/50")}
                disabled={isScanning}
              />
              {urlError && (
                <p className="text-[11px] text-destructive font-medium mt-2 flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {urlError}
                </p>
              )}
            </div>

            <Button
              id="scan-repo-btn"
              onClick={handleScan}
              disabled={isScanning || !!urlError || !repoUrl.trim()}
              className="h-12 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-bold text-sm flex items-center justify-center gap-2 shadow-lg shadow-violet-600/20 transition-all"
            >
              {isScanning ? <><Loader2 className="w-4 h-4 animate-spin" /> Scanning...</> : <><Search className="w-4 h-4" /> Scan Repository</>}
            </Button>
          </div>

          {currentStep && (
            <div className="rounded-[2rem] border border-border/50 bg-background/50 backdrop-blur-sm p-8 flex flex-col gap-4 shadow-xl shadow-primary/5">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">Pipeline Status</label>
              <div className="flex flex-col gap-1">
                {STEPS.filter(s => s !== "QUEUED").map((step, i) => {
                  const sI = STEPS.indexOf(step);
                  const isError = currentStep === "ERROR";
                  const isDone = currentStep === "DONE" ? true : sI < stepIdx;
                  const isActive = step === currentStep && !isError;
                  return (
                    <div key={step} className={cn("flex items-center gap-3 py-2.5 px-3 rounded-xl transition-all", isActive ? "bg-violet-500/8" : "")}>
                      <div className={cn("w-6 h-6 rounded-full flex items-center justify-center shrink-0 border-2 transition-all",
                        isError && sI >= stepIdx ? "border-destructive/30 text-destructive/30" :
                          isDone ? "border-emerald-500 bg-emerald-500/10 text-emerald-500" :
                            isActive ? "border-violet-500 bg-violet-500/10 text-violet-500" :
                              "border-border text-muted-foreground/30")}>
                        {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <span>{i + 1}</span>}
                      </div>
                      <span className={cn("text-xs font-semibold", isDone ? "text-emerald-500" : isActive ? "text-foreground" : "text-muted-foreground/40")}>
                        {STEP_LABELS[step]}
                      </span>
                    </div>
                  );
                })}
              </div>
              <div className="h-1.5 rounded-full bg-muted/50 overflow-hidden">
                <div className={cn("h-full rounded-full transition-all duration-700 ease-out", currentStep === "ERROR" ? "bg-destructive" : "bg-violet-500")} style={{ width: `${progress}%` }} />
              </div>
              {stepMessage && <p className="text-[11px] text-muted-foreground">{stepMessage}</p>}
            </div>
          )}
        </div>

        {/* Right Panel */}
        <div className="min-h-[500px]">
          {!result && !isScanning && !currentStep && (
            <div className="h-[480px] rounded-[2rem] border border-dashed border-border/50 flex flex-col items-center justify-center text-center gap-5 bg-muted/5">
              <BarChart3 className="w-12 h-12 text-muted-foreground/40" />
              <div>
                <p className="font-black uppercase tracking-widest text-sm">Ready to Scan</p>
                <p className="text-xs text-muted-foreground mt-1.5">Enter a public GitHub URL and click Scan Repository to begin</p>
              </div>
            </div>
          )}

          {isScanning && !result && (
            <div className="h-[480px] rounded-[2rem] border border-violet-500/20 bg-violet-500/5 flex flex-col items-center justify-center gap-6">
              <Loader2 className="w-10 h-10 animate-spin text-violet-500" />
              <p className="font-black text-xl tracking-tight">{STEP_LABELS[currentStep] || "Processing..."}</p>
            </div>
          )}

          {result && result.status === "ERROR" && (
            <div className="rounded-[2rem] border-2 border-destructive/20 bg-destructive/5 p-10 flex flex-col gap-6">
              <div className="flex items-center gap-4 text-destructive">
                <AlertCircle className="w-10 h-10" />
                <h2 className="text-2xl font-black tracking-tight">Scan Failed</h2>
              </div>
              <p className="font-mono text-sm text-destructive/80 bg-black/5 rounded-2xl p-6 border border-destructive/10 leading-relaxed">
                {result.error || "An unexpected error occurred during scanning."}
              </p>
            </div>
          )}

          {result && result.status === "DONE" && (
            <div className="space-y-7">
              <div className="rounded-[2rem] border border-emerald-500/20 bg-emerald-500/5 p-7 flex flex-wrap items-center gap-6">
                <CheckCircle2 className="w-7 h-7 text-emerald-500" />
                <div className="flex-1 min-w-0">
                  <h2 className="text-2xl font-black tracking-tight">{result.owner}/{result.repo}</h2>
                  <p className="text-sm text-muted-foreground mt-1">
                    Detected by <span className="font-bold text-violet-500">{result.detection_tool}</span>
                    {" · "}{result.total_files} files{" · "}{result.scan_duration_seconds}s
                  </p>
                </div>
                <div className="flex gap-2">
                  <Badge variant="outline" className="bg-violet-500/10 text-violet-500 border-violet-500/20 font-bold">
                    {langEntries.length} Languages
                  </Badge>
                  <Badge variant="outline" className="font-bold bg-orange-500/10 text-orange-500 border-orange-500/20">
                    {result.total_findings} Findings
                  </Badge>
                </div>
              </div>

              {chartData.length > 0 && (
                <div className="rounded-[2rem] border border-border/50 bg-background/50 p-8">
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-6">Language Distribution</h3>
                  <div className="h-[220px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chartData} layout="vertical" margin={{ left: 90, right: 40 }}>
                        <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fontSize: 10 }} />
                        <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 11, fontWeight: 700 }} />
                        <Tooltip formatter={(v: any) => [`${v}%`, "Share"]} />
                        <Bar dataKey="%" radius={[0, 8, 8, 0]} maxBarSize={28}>
                          {chartData.map((_, i) => <Cell key={i} fill={LANG_COLORS[i % LANG_COLORS.length]} />)}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              <div className="space-y-3">
                {langEntries.map(([lang, info], i) => (
                  <div key={lang} className="rounded-2xl border border-border/50 bg-background/40 overflow-hidden">
                    <button
                      className="w-full p-5 flex items-center gap-4 text-left hover:bg-muted/10 transition-colors"
                      onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                    >
                      <span className="w-3 h-3 rounded-full" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                      <span className="font-black text-base flex-1">{lang}</span>
                      <div className="flex items-center gap-6 text-right mr-2">
                        <div>
                          <p className="text-[10px] text-muted-foreground">Files</p>
                          <p className="font-black text-sm">{info.file_count}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-muted-foreground">LoC</p>
                          <p className="font-black text-sm">{info.lines_of_code.toLocaleString()}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-muted-foreground">Share</p>
                          <p className="font-black text-sm">{info.percentage.toFixed(1)}%</p>
                        </div>
                      </div>
                      {info.findings.length > 0 && (
                        <Badge variant="outline" className="text-[9px] font-black bg-orange-500/10 text-orange-500 border-orange-500/20 py-0">
                          {info.findings.length} issues
                        </Badge>
                      )}
                    </button>

                    {expandedLang === lang && (
                      <div className="border-t border-border/50">
                        {info.findings.length === 0 ? (
                          <div className="p-5 flex items-center gap-2 text-muted-foreground/60">
                            <Shield className="w-4 h-4" />
                            <span className="text-xs font-semibold">No security findings for this language</span>
                          </div>
                        ) : (
                          <div
                            className="overflow-y-auto p-5 space-y-3"
                            style={{ maxHeight: "520px" }}
                          >
                            {info.findings.map((f, fi) => {
                              const sev = f.severity?.toUpperCase() ?? "INFO";
                              const sevColor =
                                sev === "CRITICAL" ? "border-red-500/60 bg-red-500/5" :
                                  sev === "HIGH" ? "border-orange-500/60 bg-orange-500/5" :
                                    sev === "MEDIUM" ? "border-yellow-500/60 bg-yellow-500/5" :
                                      sev === "LOW" ? "border-blue-500/60 bg-blue-500/5" :
                                        "border-border/50 bg-muted/10";
                              const badgeColor =
                                sev === "CRITICAL" ? "bg-red-500/15 text-red-500 border-red-500/30" :
                                  sev === "HIGH" ? "bg-orange-500/15 text-orange-500 border-orange-500/30" :
                                    sev === "MEDIUM" ? "bg-yellow-500/15 text-yellow-600 border-yellow-500/30" :
                                      sev === "LOW" ? "bg-blue-500/15 text-blue-500 border-blue-500/30" :
                                        "bg-muted text-muted-foreground border-border";
                              return (
                                <div key={fi} className={cn("p-4 rounded-xl border", sevColor)}>
                                  <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                                    <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide", badgeColor)}>
                                      {sev}
                                    </Badge>
                                    <span className="text-[10px] font-bold text-muted-foreground">{f.tool}</span>
                                    {f.line && (
                                      <span className="text-[9px] font-mono text-muted-foreground/50 ml-auto">L:{f.line}</span>
                                    )}
                                  </div>
                                  <p className="text-sm font-semibold leading-snug">{f.issue}</p>
                                  {f.file && (
                                    <div className="flex items-center gap-1.5 mt-2">
                                      <FileCode className="w-3 h-3 text-muted-foreground/40 shrink-0" />
                                      <p className="text-[10px] font-mono text-muted-foreground/50 truncate">{f.file}</p>
                                    </div>
                                  )}
                                  {f.remediation && (
                                    <p className="text-[10px] text-muted-foreground/60 mt-1.5 leading-relaxed">{f.remediation}</p>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
