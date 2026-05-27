import { useState, useEffect, useRef } from "react";
import { Github, Search, CheckCircle2, AlertCircle, Loader2, X, BarChart3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

interface RepoScannerWidgetProps {
  apiBaseUrl: string;
  keys: { id: string; backend: string }[];
}

interface ScanEvent {
  job_id: string;
  step: string;
  message: string;
  progress: number;
  detail?: any;
}

interface LanguageResult {
  language: string;
  file_count: number;
  lines_of_code: number;
  percentage: number;
  findings: { severity: string; file: string; line?: number; issue: string; tool: string; remediation?: string }[];
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

const STEPS = ["QUEUED", "PROVISIONING", "CLONING", "DETECTING", "SCANNING", "DONE"];
const STEP_LABELS: Record<string, string> = {
  QUEUED: "Queued",
  PROVISIONING: "Provisioning sandbox...",
  CLONING: "Cloning repository...",
  DETECTING: "Detecting languages...",
  SCANNING: "Scanning files...",
  DONE: "Done",
};

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

const GITHUB_PATTERN = /^https:\/\/github\.com\/[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+\/?$/;

export default function RepoScannerWidget({ apiBaseUrl, keys }: RepoScannerWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [currentStep, setCurrentStep] = useState("");
  const [stepMessage, setStepMessage] = useState("");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const getApiKey = () => {
    for (const k of keys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) return saved;
    }
    return null;
  };

  const validateUrl = (url: string) => {
    if (!url) { setUrlError(""); return; }
    if (!GITHUB_PATTERN.test(url.trim())) {
      setUrlError("Must be a valid GitHub URL: https://github.com/owner/repo");
    } else {
      setUrlError("");
    }
  };

  const reset = () => {
    setCurrentStep(""); setStepMessage(""); setProgress(0);
    setResult(null); setExpandedLang(null);
    if (esRef.current) { esRef.current.close(); esRef.current = null; }
  };

  const handleScan = async () => {
    const url = repoUrl.trim();
    if (!GITHUB_PATTERN.test(url)) {
      setUrlError("Enter a valid public GitHub URL"); return;
    }
    const apiKey = getApiKey();
    if (!apiKey) {
      toast.error("No API key found. Please create one in API Management tab."); return;
    }

    reset();
    setIsScanning(true);

    try {
      const resp = await fetch(`${apiBaseUrl}/v1/repo-scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ repo_url: url }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start scan");

      const { job_id } = data;

      // Connect to SSE stream
      const es = new EventSource(`${apiBaseUrl}/v1/repo-scan/${job_id}/status?token=${encodeURIComponent(apiKey)}`);
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const event: ScanEvent = JSON.parse(e.data);
          setCurrentStep(event.step);
          setStepMessage(event.message);
          setProgress(event.progress);

          if (event.step === "DONE") {
            if (event.detail) setResult(event.detail as ScanResult);
            setIsScanning(false);
            es.close();
          } else if (event.step === "ERROR") {
            toast.error(event.message);
            setIsScanning(false);
            es.close();
          }
        } catch { /* ignore parse errors */ }
      };

      es.onerror = () => {
        es.close();
        setIsScanning(false);
        // Fallback: fetch result directly
        fetch(`${apiBaseUrl}/v1/repo-scan/${job_id}/result`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        }).then(r => r.json()).then(d => setResult(d)).catch(() => {});
      };
    } catch (err: any) {
      toast.error(err.message);
      setIsScanning(false);
    }
  };

  useEffect(() => () => { esRef.current?.close(); }, []);

  const langEntries = result ? Object.entries(result.languages) : [];
  const chartData = langEntries.map(([lang, r]) => ({
    name: lang, value: parseFloat(r.percentage.toFixed(1)),
  }));

  const stepIndex = STEPS.indexOf(currentStep);
  const doneStepIndex = STEPS.indexOf("DONE");

  return (
    <>
      {/* Dashboard Card */}
      <Card className="group relative overflow-hidden rounded-[2rem] border-border/50 bg-background/50 backdrop-blur-sm transition-all hover:border-violet-500/40 hover:shadow-2xl hover:shadow-violet-500/5">
        <CardHeader className="p-8 pb-4">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-3 rounded-2xl border bg-violet-500/10 text-violet-500 border-violet-500/20">
              <Github className="w-6 h-6" />
            </div>
            <CardTitle className="text-2xl font-black">Repo Scanner</CardTitle>
          </div>
          <CardDescription className="text-base text-muted-foreground">
            Deep-scan any public GitHub repo — language detection, LoC analysis, and static security findings.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-8 pb-8 flex flex-col gap-3 min-h-[100px] justify-end">
          <Button
            className="w-full bg-violet-500/10 hover:bg-violet-500/20 text-violet-500 border border-violet-500/20 rounded-2xl h-12 font-bold flex items-center gap-2 transition-all"
            onClick={() => setIsOpen(true)}
          >
            <Search className="w-4 h-4" />
            Scan Repository
          </Button>
        </CardContent>
      </Card>

      {/* Full Scanner Dialog */}
      <Dialog open={isOpen} onOpenChange={(o) => { if (!o) { reset(); } setIsOpen(o); }}>
        <DialogContent className="max-w-[100vw] w-screen h-screen m-0 p-0 overflow-hidden border-none bg-background flex flex-col rounded-none">

          {/* Header */}
          <DialogHeader className="px-10 py-7 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl bg-violet-500/10 border border-violet-500/20 text-violet-500">
                <Github className="w-5 h-5" />
              </div>
              <div>
                <DialogTitle className="text-xl font-black tracking-tight">GitHub Repository Scanner</DialogTitle>
                <DialogDescription className="text-[11px] font-bold text-muted-foreground uppercase tracking-[0.25em] flex items-center gap-2 mt-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
                  linguist · tokei · enry · static analysis
                </DialogDescription>
              </div>
            </div>
            <button onClick={() => { reset(); setIsOpen(false); }} className="p-2 rounded-xl hover:bg-muted/50 transition-colors text-muted-foreground">
              <X className="w-5 h-5" />
            </button>
          </DialogHeader>

          <div className="flex-1 flex overflow-hidden">

            {/* Left Panel — Input + Status */}
            <div className="w-[400px] shrink-0 flex flex-col p-8 border-r border-border/50 bg-muted/10 gap-6">

              {/* URL Input */}
              <div className="flex flex-col gap-2">
                <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">Repository URL</label>
                <Input
                  id="repo-url-input"
                  value={repoUrl}
                  onChange={(e) => { setRepoUrl(e.target.value); validateUrl(e.target.value); }}
                  onBlur={() => validateUrl(repoUrl)}
                  placeholder="https://github.com/owner/repo"
                  className={cn("rounded-xl h-11 font-mono text-sm border transition-colors", urlError ? "border-destructive" : "")}
                  disabled={isScanning}
                />
                {urlError && <p className="text-[11px] text-destructive font-medium">{urlError}</p>}
              </div>

              <Button
                id="scan-repo-btn"
                onClick={handleScan}
                disabled={isScanning || !!urlError || !repoUrl}
                className="h-11 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-bold flex items-center gap-2 shadow-lg shadow-violet-600/20 transition-all disabled:opacity-60"
              >
                {isScanning ? <><Loader2 className="w-4 h-4 animate-spin" /> Scanning...</> : <><Search className="w-4 h-4" /> Scan Repository</>}
              </Button>

              {/* Step Timeline */}
              {currentStep && (
                <div className="flex flex-col gap-1 mt-2 animate-in fade-in slide-in-from-bottom-4 duration-300">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-3">Pipeline Status</label>
                  {STEPS.filter(s => s !== "QUEUED").map((step, i) => {
                    const stepI = STEPS.indexOf(step);
                    const isDone = currentStep === "DONE" ? true : stepI < stepIndex;
                    const isActive = step === currentStep;
                    const isError = currentStep === "ERROR" && step === "SCANNING";
                    return (
                      <div key={step} className={cn("flex items-center gap-3 py-2 px-3 rounded-xl transition-all", isActive ? "bg-violet-500/10" : "")}>
                        <div className={cn("w-5 h-5 rounded-full flex items-center justify-center shrink-0 text-[10px] font-black border transition-all",
                          isError ? "border-destructive text-destructive bg-destructive/10" :
                          isDone ? "border-emerald-500 bg-emerald-500/10 text-emerald-500" :
                          isActive ? "border-violet-500 bg-violet-500/10 text-violet-500" :
                          "border-border text-muted-foreground/30")}>
                          {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <span>{i + 1}</span>}
                        </div>
                        <span className={cn("text-xs font-semibold", isActive ? "text-foreground" : isDone ? "text-emerald-500" : "text-muted-foreground/40")}>
                          {STEP_LABELS[step]}
                        </span>
                      </div>
                    );
                  })}

                  {/* Progress bar */}
                  <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
                    <div className="h-full bg-violet-500 rounded-full transition-all duration-700 ease-out" style={{ width: `${progress}%` }} />
                  </div>
                  {stepMessage && (
                    <p className="text-[11px] text-muted-foreground mt-2 leading-relaxed">{stepMessage}</p>
                  )}
                </div>
              )}

              {/* Error state */}
              {currentStep === "ERROR" && (
                <div className="p-4 rounded-xl bg-destructive/10 border border-destructive/20 text-destructive text-xs font-medium flex items-start gap-2 animate-in fade-in duration-300">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{stepMessage}</span>
                </div>
              )}
            </div>

            {/* Right Panel — Results */}
            <div className="flex-1 overflow-hidden flex flex-col bg-background">
              {!result && !isScanning && (
                <div className="flex-1 flex flex-col items-center justify-center text-center gap-4 opacity-30">
                  <BarChart3 className="w-16 h-16 text-muted-foreground" />
                  <div>
                    <p className="font-black uppercase tracking-widest text-sm">Awaiting Scan</p>
                    <p className="text-xs text-muted-foreground mt-1">Enter a public GitHub repository URL and click Scan Repository</p>
                  </div>
                </div>
              )}

              {isScanning && !result && (
                <div className="flex-1 flex flex-col items-center justify-center gap-6 animate-in fade-in duration-500">
                  <div className="relative">
                    <div className="w-16 h-16 rounded-full border-2 border-violet-500/20 animate-ping absolute inset-0" />
                    <div className="w-16 h-16 rounded-full border-2 border-violet-500/40 flex items-center justify-center relative">
                      <Github className="w-7 h-7 text-violet-500 animate-pulse" />
                    </div>
                  </div>
                  <div className="text-center">
                    <p className="font-black text-lg uppercase tracking-tight">{STEP_LABELS[currentStep] || "Processing..."}</p>
                    <p className="text-xs text-muted-foreground mt-1 max-w-xs">{stepMessage}</p>
                  </div>
                </div>
              )}

              {result && result.status === "DONE" && (
                <ScrollArea className="flex-1">
                  <div className="p-8 space-y-8 max-w-4xl mx-auto">

                    {/* Summary bar */}
                    <div className="flex flex-wrap items-center gap-4 p-5 rounded-2xl bg-emerald-500/5 border border-emerald-500/20">
                      <CheckCircle2 className="w-6 h-6 text-emerald-500 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="font-black text-base">{result.owner}/{result.repo}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {result.detection_tool} · {result.total_files} files · {result.scan_duration_seconds}s
                        </p>
                      </div>
                      <div className="flex gap-3 flex-wrap">
                        <Badge variant="outline" className="bg-violet-500/10 text-violet-500 border-violet-500/20 font-bold">
                          {langEntries.length} Languages
                        </Badge>
                        <Badge variant="outline" className={cn("font-bold", result.total_findings > 0 ? "bg-orange-500/10 text-orange-500 border-orange-500/20" : "bg-emerald-500/10 text-emerald-500 border-emerald-500/20")}>
                          {result.total_findings} Findings
                        </Badge>
                      </div>
                    </div>

                    {/* Language Bar Chart */}
                    {chartData.length > 0 && (
                      <div>
                        <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-4">Language Distribution</h3>
                        <div className="h-[200px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} layout="vertical" margin={{ left: 80, right: 30, top: 0, bottom: 0 }}>
                              <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fontSize: 10 }} />
                              <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fontWeight: 700 }} width={80} />
                              <Tooltip formatter={(v: any) => [`${v}%`, "Share"]} contentStyle={{ borderRadius: 12, border: "1px solid hsl(var(--border))", background: "hsl(var(--background))", fontSize: 11 }} />
                              <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                                {chartData.map((_, i) => <Cell key={i} fill={LANG_COLORS[i % LANG_COLORS.length]} />)}
                              </Bar>
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    )}

                    {/* Per-language cards */}
                    <div>
                      <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-4">Per-Language Results</h3>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {langEntries.map(([lang, info], i) => (
                          <div key={lang} className="rounded-2xl border border-border/50 bg-muted/10 hover:bg-muted/20 transition-all">
                            <button
                              className="w-full p-5 flex items-center justify-between text-left"
                              onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                            >
                              <div className="flex items-center gap-3">
                                <span className="w-3 h-3 rounded-full shrink-0" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                                <span className="font-black text-sm">{lang}</span>
                              </div>
                              <div className="flex items-center gap-3 text-right">
                                <div className="text-right">
                                  <p className="text-[10px] text-muted-foreground">{info.file_count} files · {info.lines_of_code.toLocaleString()} LoC</p>
                                  <p className="text-xs font-bold">{info.percentage.toFixed(1)}%</p>
                                </div>
                                {info.findings.length > 0 && (
                                  <Badge variant="outline" className="text-[9px] font-black bg-orange-500/10 text-orange-500 border-orange-500/20 py-0">
                                    {info.findings.length}
                                  </Badge>
                                )}
                              </div>
                            </button>
                            {expandedLang === lang && info.findings.length > 0 && (
                              <div className="border-t border-border/50 p-4 space-y-2 animate-in fade-in slide-in-from-top-2 duration-200">
                                {info.findings.slice(0, 5).map((f, fi) => {
                                  const sev = f.severity.toUpperCase();
                                  const sevCls = sev === "CRITICAL" ? "text-red-500 bg-red-500/10 border-red-500/20" : sev === "HIGH" ? "text-orange-500 bg-orange-500/10 border-orange-500/20" : sev === "MEDIUM" ? "text-amber-500 bg-amber-500/10 border-amber-500/20" : "text-blue-500 bg-blue-500/10 border-blue-500/20";
                                  return (
                                    <div key={fi} className="p-3 rounded-xl bg-background/50 border border-border/40 text-xs">
                                      <div className="flex items-center gap-2 mb-1.5">
                                        <Badge variant="outline" className={cn("text-[8px] font-black py-0 h-4", sevCls)}>{sev}</Badge>
                                        <span className="text-muted-foreground font-mono">{f.tool}</span>
                                        {f.line && <span className="text-muted-foreground/50 font-mono">L:{f.line}</span>}
                                      </div>
                                      <p className="font-semibold text-foreground/80">{f.issue}</p>
                                      {f.file && <p className="text-muted-foreground/50 font-mono mt-1 truncate">{f.file}</p>}
                                    </div>
                                  );
                                })}
                                {info.findings.length > 5 && (
                                  <p className="text-[10px] text-muted-foreground text-center pt-1">+{info.findings.length - 5} more findings</p>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </ScrollArea>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
