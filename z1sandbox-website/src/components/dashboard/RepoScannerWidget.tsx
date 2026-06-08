import { useState, useEffect, useRef } from "react";
import { Github, Search, CheckCircle2, AlertCircle, Loader2, X, BarChart3, Activity } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { useJobStore } from "@/hooks/useJobStore";
import { JobsPanel } from "./JobsPanel";
import { UnifiedPipelineView } from "./UnifiedPipelineView";

interface RepoScannerWidgetProps {
  apiBaseUrl: string;
  keys: { id: string; backend: string }[];
}

const REPO_SCAN_STEPS = [
  { key: "QUEUED", label: "Job Queued" },
  { key: "PROVISIONING", label: "Provisioning sandbox environment..." },
  { key: "CLONING", label: "Cloning repository..." },
  { key: "DETECTING", label: "Detecting languages..." },
  { key: "SCANNING", label: "Running security scan..." },
  { key: "DONE", label: "Scan complete" },
];

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
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);

  const getApiKey = () => {
    for (const k of keys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) return saved;
    }
    return null;
  };

  const apiKey = getApiKey() || "";

  // Initialize unified hook
  const {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    syncFromServer,
  } = useJobStore("repo-scan", apiBaseUrl, apiKey);

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

  const validateUrl = (url: string) => {
    if (!url) { setUrlError(""); return; }
    if (!GITHUB_PATTERN.test(url.trim())) {
      setUrlError("Must be a valid GitHub URL: https://github.com/owner/repo");
    } else {
      setUrlError("");
    }
  };

  const handleScan = async () => {
    const url = repoUrl.trim();
    if (!GITHUB_PATTERN.test(url)) {
      setUrlError("Enter a valid public GitHub URL"); return;
    }

    if (!apiKey) {
      toast.error("No API key found. Please create one in API Management tab."); return;
    }

    try {
      setIsScanning(true);

      const resp = await fetch(`${apiBaseUrl}/v1/repo-scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ repo_url: url }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start scan");

      const { job_id, repo_url: data_repo_url, submitted_at } = data;

      // Add to store
      addJob({
        job_id,
        job_type: "repo-scan",
        status: "QUEUED",
        progress: 5,
        stepMessage: "Job queued",
        eventIndex: 0,
        metadata: {
          repo_url: data_repo_url || url,
          submitted_at: submitted_at || new Date().toISOString(),
        },
        summary: null,
        result: null,
        submittedAt: submitted_at || new Date().toISOString(),
        completedAt: null
      });

      setSelectedJobId(job_id);
      openStream(job_id, 0);
      // Force immediate sync so concurrent CLI-triggered repo scans surface at once
      syncFromServer();

      toast.success("Repository scan initiated!");
    } catch (err: any) {
      toast.error(err.message || "Failed to start scan");
    } finally {
      setIsScanning(false);
    }
  };

  // Find currently selected job record
  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || null;
  const selectedResult = selectedJobId ? volatileResults[selectedJobId] : null;

  // Custom renderer for scan result findings
  const renderRepoScanResult = (result: any) => {
    const langEntries = result && result.languages ? Object.entries(result.languages) : [];
    const chartData = langEntries.map(([lang, r]: [string, any]) => ({
      name: lang, value: r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
    }));

    return (
      <div className="space-y-8 mt-4 animate-in fade-in duration-500">
        {/* Summary Info Banner */}
        <div className="flex flex-wrap items-center gap-4 p-5 rounded-2xl bg-emerald-500/5 border border-emerald-500/20 shadow-sm">
          <CheckCircle2 className="w-6 h-6 text-emerald-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-black text-base text-foreground">{result.owner}/{result.repo}</p>
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

        {/* Language distribution chart */}
        {chartData.length > 0 && (
          <div className="bg-muted/5 border border-border/40 p-5 rounded-2xl">
            <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-4">Language Distribution</h3>
            <div className="h-[180px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} layout="vertical" margin={{ left: 50, right: 30, top: 0, bottom: 0 }}>
                  <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fontWeight: 700 }} width={70} />
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
          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-4">Per-Language Details</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {langEntries.map(([lang, info]: [string, any], i) => (
              <div key={lang} className="rounded-2xl border border-border/40 bg-card hover:bg-muted/15 transition-all overflow-hidden">
                <button
                  className="w-full p-4 flex items-center justify-between text-left"
                  onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                >
                  <div className="flex items-center gap-3">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                    <span className="font-black text-xs text-foreground">{lang}</span>
                  </div>
                  <div className="flex items-center gap-3 text-right">
                    <div className="text-right">
                      <p className="text-[9px] text-muted-foreground">{info.file_count} files · {info.lines_of_code.toLocaleString()} LoC</p>
                      <p className="text-xs font-bold text-foreground">{info.percentage.toFixed(1)}%</p>
                    </div>
                    {info.findings.length > 0 && (
                      <Badge variant="outline" className="text-[9px] font-black bg-orange-500/10 text-orange-500 border-orange-500/20 py-0">
                        {info.findings.length}
                      </Badge>
                    )}
                  </div>
                </button>
                {expandedLang === lang && (
                  <div className="border-t border-border/20 bg-muted/5">
                    {info.findings.length === 0 ? (
                      <div className="p-4 text-xs font-semibold text-muted-foreground/60 text-center">
                        No security findings for this language
                      </div>
                    ) : (
                      <div className="overflow-y-auto p-4 space-y-3 max-h-[350px]">
                        {info.findings.map((f: any, fi: number) => {
                          const sev = f.severity?.toUpperCase() ?? "INFO";
                          const sevColor =
                            sev === "CRITICAL" ? "border-red-500/60 bg-red-500/5" :
                            sev === "HIGH"     ? "border-orange-500/60 bg-orange-500/5" :
                            sev === "MEDIUM"   ? "border-yellow-500/60 bg-yellow-500/5" :
                            sev === "LOW"      ? "border-blue-500/60 bg-blue-500/5" :
                                                 "border-border/50 bg-muted/10";
                          const badgeColor =
                            sev === "CRITICAL" ? "bg-red-500/15 text-red-500 border-red-500/30" :
                            sev === "HIGH"     ? "bg-orange-500/15 text-orange-500 border-orange-500/30" :
                            sev === "MEDIUM"   ? "bg-yellow-500/15 text-yellow-600 border-yellow-500/30" :
                            sev === "LOW"      ? "bg-blue-500/15 text-blue-500 border-blue-500/30" :
                                                 "bg-muted text-muted-foreground border-border";
                          return (
                            <div key={fi} className={cn("p-3 rounded-xl border text-[11px] transition-all", sevColor)}>
                              <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                                <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide", badgeColor)}>
                                  {sev}
                                </Badge>
                                <span className="text-[9px] font-bold text-muted-foreground">{f.tool}</span>
                                {f.line && (
                                  <span className="text-[9px] font-mono text-muted-foreground/50 ml-auto">L:{f.line}</span>
                                )}
                              </div>
                              <p className="font-bold text-foreground leading-snug">{f.issue}</p>
                              {f.file && (
                                <div className="flex items-center gap-1.5 mt-1.5">
                                  <p className="text-[9px] font-mono text-muted-foreground/50 truncate">{f.file}</p>
                                </div>
                              )}
                              {f.remediation && (
                                <p className="text-[9px] text-muted-foreground/60 mt-1 leading-relaxed">{f.remediation}</p>
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
      </div>
    );
  };

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
      <Dialog open={isOpen} onOpenChange={(o) => { if (!o) { setSelectedJobId(null); } setIsOpen(o); }}>
        <DialogContent className="max-w-[100vw] w-screen h-screen m-0 p-0 overflow-hidden border-none bg-background flex flex-col rounded-none">

          {/* Header */}
          <DialogHeader className="px-8 py-5 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
            <div className="flex items-center gap-4">
              <div className="p-2 rounded-xl bg-violet-500/10 border border-violet-500/20 text-violet-500">
                <Github className="w-5 h-5" />
              </div>
              <div>
                <DialogTitle className="text-lg font-black tracking-tight">GitHub Repository Scanner</DialogTitle>
                <DialogDescription className="text-[10px] font-bold text-muted-foreground uppercase tracking-[0.25em] flex items-center gap-2 mt-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
                  linguist · tokei · enry · static analysis
                </DialogDescription>
              </div>
            </div>
            <button onClick={() => { setSelectedJobId(null); setIsOpen(false); }} className="p-2 rounded-xl hover:bg-muted/50 transition-colors text-muted-foreground">
              <X className="w-5 h-5" />
            </button>
          </DialogHeader>

          {/* Triple Panel Layout */}
          <div className="flex-1 flex overflow-hidden">

            {/* Panel 1: Input URL and Submission Controls (left) */}
            <div className="w-[400px] shrink-0 flex flex-col p-6 border-r border-border/50 bg-muted/5 gap-5">
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
                className="h-11 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-bold flex items-center gap-2 shadow-lg shadow-violet-600/15 transition-all disabled:opacity-60"
              >
                {isScanning ? <><Loader2 className="w-4 h-4 animate-spin" /> Ingesting...</> : <><Search className="w-4 h-4" /> Scan Repository</>}
              </Button>
            </div>

            {/* Panel 2: Sidebar list panel (middle) */}
            <JobsPanel
              jobs={jobs}
              selectedJobId={selectedJobId}
              onSelectJob={setSelectedJobId}
              onDeleteJob={removeJob}
              jobType="repo-scan"
            />

            {/* Panel 3: Execution View / Result renderer (right) */}
            <div className="flex-1 bg-background overflow-hidden flex flex-col p-6">
              <ScrollArea className="flex-1">
                <div className="max-w-5xl mx-auto w-full">
                  {selectedJob ? (
                    <UnifiedPipelineView
                      job={selectedJob}
                      steps={REPO_SCAN_STEPS}
                      result={selectedResult}
                      onResultRender={renderRepoScanResult}
                    />
                  ) : (
                    <div className="h-[60vh] flex flex-col items-center justify-center text-center gap-4 opacity-40">
                      <Activity className="w-12 h-12 text-muted-foreground animate-pulse" />
                      <div>
                        <h3 className="text-sm font-black uppercase tracking-wider">No Scan Selected</h3>
                        <p className="text-xs text-muted-foreground mt-1">
                          Select a repository scan job from the panel or run a new scan.
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
    </>
  );
}
