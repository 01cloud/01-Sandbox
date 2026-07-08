import { useState, useEffect } from "react";
import {
  X,
  ShieldCheck,
  Zap,
  AlertCircle,
  FileCode,
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Shield
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useJobStore } from "@/hooks/useJobStore";
import { UnifiedPipelineView } from "./UnifiedPipelineView";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell
} from "recharts";

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

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
  const [expandedLang, setExpandedLang] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "telemetry">("dashboard");

  // Initialize unified hook
  const {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    syncFromServer,
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
      // Force immediate sync so any concurrent CLI-triggered jobs surface at once
      syncFromServer();

      toast.success("Security audit pipeline initiated!");
    } catch (error: any) {
      console.error("Scan error:", error);
      toast.error(error.message || "Audit failed to initiate");
    } finally {
      setIsScanning(false);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    if (!apiKey) {
      toast.error("No API key found.");
      return;
    }
    try {
      const response = await fetch(`${baseUrl}/v1/jobs/${jobId}`, {
        method: "DELETE",
        headers: {
          "Authorization": `Bearer ${apiKey}`
        }
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || "Failed to cancel job");
      }
      toast.success("Job cancellation requested.");
      syncFromServer();
    } catch (error: any) {
      console.error("Cancel job error:", error);
      toast.error(error.message || "Failed to cancel job");
    }
  };

  // Find currently selected job record
  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || null;
  const selectedResult = selectedJobId ? volatileResults[selectedJobId] : null;

  const generateSyntheticLanguages = (filesScanned: string[], findings: any[]): Record<string, any> => {
    const languages: Record<string, any> = {};
    const files = filesScanned && filesScanned.length > 0 ? filesScanned : ["input.py"];

    files.forEach(filePath => {
      const ext = filePath.split('.').pop()?.toLowerCase();
      let langName = "Other";
      if (ext === "py") langName = "Python";
      else if (ext === "go") langName = "Go";
      else if (ext === "js" || ext === "jsx") langName = "JavaScript";
      else if (ext === "ts" || ext === "tsx") langName = "TypeScript";
      else if (ext === "sh" || ext === "bash") langName = "Shell";
      else if (ext === "json") langName = "JSON";
      else if (ext === "yaml" || ext === "yml") {
        const hasK8sTool = findings.some(f => {
          const fileVal = f.file || "";
          const toolVal = f.tool || "";
          return fileVal === filePath && ["kubelinter", "kubeconform", "kubescore"].includes(toolVal.toLowerCase());
        });
        langName = hasK8sTool ? "Kubernetes" : "YAML";
      }

      if (!languages[langName]) {
        languages[langName] = {
          language: langName,
          file_count: 0,
          lines_of_code: code.split('\n').length || 0,
          percentage: 0,
          findings: []
        };
      }
      languages[langName].file_count += 1;
    });

    findings.forEach(finding => {
      const filePath = finding.file || "input.py";
      const ext = filePath.split('.').pop()?.toLowerCase();
      let langName = "Other";
      if (ext === "py") langName = "Python";
      else if (ext === "go") langName = "Go";
      else if (ext === "js" || ext === "jsx") langName = "JavaScript";
      else if (ext === "ts" || ext === "tsx") langName = "TypeScript";
      else if (ext === "sh" || ext === "bash") langName = "Shell";
      else if (ext === "json") langName = "JSON";
      else if (ext === "yaml" || ext === "yml") {
        const toolVal = finding.tool || "";
        const isK8s = ["kubelinter", "kubeconform", "kubescore"].includes(toolVal.toLowerCase());
        langName = isK8s ? "Kubernetes" : "YAML";
      }

      if (languages[langName]) {
        languages[langName].findings.push(finding);
      } else {
        languages[langName] = {
          language: langName,
          file_count: 1,
          lines_of_code: code.split('\n').length || 0,
          percentage: 0,
          findings: [finding]
        };
      }
    });

    const totalFiles = files.length || 1;
    Object.keys(languages).forEach(key => {
      languages[key].percentage = parseFloat(((languages[key].file_count / totalFiles) * 100).toFixed(1));
    });

    return languages;
  };

  // Custom renderer for scan result findings
  const renderQuickScanResult = (result: any) => {
    const normalized = result.report || result;
    const findings = normalized.findings || [];
    const filesScanned = normalized.files_scanned || ["input." + detectLanguage(code)];
    const totalFindings = findings.length;

    const syntheticLangs = generateSyntheticLanguages(filesScanned, findings);
    const langEntries = Object.entries(syntheticLangs);

    const chartData = langEntries.map(([lang, r]) => ({
      name: lang,
      "%": r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
    }));

    return (
      <div className="space-y-7 animate-in fade-in duration-500">
        <div className={cn(
          "rounded-[2rem] border p-7 flex flex-wrap items-center gap-6 shadow-sm",
          totalFindings === 0
            ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-400"
            : "border-destructive/20 bg-destructive/5 text-destructive"
        )}>
          {totalFindings === 0 ? (
            <CheckCircle2 className="w-7 h-7 text-emerald-500" />
          ) : (
            <AlertCircle className="w-7 h-7 text-destructive" />
          )}
          <div className="flex-1 min-w-0">
            <h2 className="text-2xl font-black tracking-tight text-foreground">
              {totalFindings === 0 ? "SCAN VERDICT: SECURE" : "VULNERABILITIES DETECTED"}
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              Detected by <span className="font-bold text-violet-500">Unified Ingestion Pipeline</span>
              {" · "}{filesScanned.length} files scanned{" · "}{code.split('\n').length} lines of code
            </p>
          </div>
          <div className="flex gap-2">
            <Badge variant="outline" className="bg-violet-500/10 text-violet-500 border-violet-500/20 font-bold">
              {langEntries.length} {langEntries.length === 1 ? "Language" : "Languages"}
            </Badge>
            <Badge variant="outline" className={cn("font-bold", totalFindings === 0 ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" : "bg-orange-500/10 text-orange-500 border-orange-500/20")}>
              {totalFindings} Findings
            </Badge>
          </div>
        </div>

        {/* Tab Toggle */}
        <div className="flex bg-muted/40 p-1 rounded-xl border border-border/40 max-w-md">
          <button
            type="button"
            onClick={() => setActiveTab("dashboard")}
            className={cn(
              "flex-1 py-1.5 px-4 text-xs font-black uppercase tracking-wider rounded-lg transition-all",
              activeTab === "dashboard"
                ? "bg-violet-600 text-white shadow-md shadow-violet-600/10"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Executive Dashboard
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("telemetry")}
            className={cn(
              "flex-1 py-1.5 px-4 text-xs font-black uppercase tracking-wider rounded-lg transition-all",
              activeTab === "telemetry"
                ? "bg-violet-600 text-white shadow-md shadow-violet-600/10"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Developer Telemetry
          </button>
        </div>

        {activeTab === "dashboard" ? (
          <div className="space-y-7 animate-in fade-in duration-300">
            {chartData.length > 0 && (
              <div className="rounded-[2rem] border border-border/50 bg-background/50 p-8 shadow-sm">
                <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-6">Language Distribution</h3>
                <div className="h-[200px]">
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
              {langEntries.map(([lang, info]: [string, any], i) => {
                const sevCounts = info.findings.reduce(
                  (acc: any, f: any) => {
                    const sev = (f.severity || "INFO").toUpperCase();
                    if (sev === "CRITICAL") {
                      acc.critical = (acc.critical || 0) + 1;
                    } else if (sev === "HIGH") {
                      acc.high = (acc.high || 0) + 1;
                    } else if (sev === "MEDIUM") {
                      acc.medium = (acc.medium || 0) + 1;
                    } else if (sev === "LOW") {
                      acc.low = (acc.low || 0) + 1;
                    } else if (sev === "INFO") {
                      acc.info = (acc.info || 0) + 1;
                    }
                    return acc;
                  },
                  { critical: 0, high: 0, medium: 0, low: 0, info: 0 }
                );

                return (
                  <div key={lang} className="rounded-2xl border border-border/50 bg-background/40 overflow-hidden shadow-sm">
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
                          <p className="text-[10px] text-muted-foreground mb-0.5">Severity</p>
                          <div className="flex gap-1 items-center">
                            {sevCounts.critical > 0 && (
                              <Badge className="h-4 px-1 text-[8px] bg-red-600/25 hover:bg-red-600/25 text-red-500 border border-red-500/35 font-extrabold rounded-md">
                                C:{sevCounts.critical}
                              </Badge>
                            )}
                            {sevCounts.high > 0 && (
                              <Badge className="h-4 px-1 text-[8px] bg-orange-500/20 hover:bg-orange-500/20 text-orange-500 border border-orange-500/30 font-extrabold rounded-md">
                                H:{sevCounts.high}
                              </Badge>
                            )}
                            {sevCounts.medium > 0 && (
                              <Badge className="h-4 px-1 text-[8px] bg-yellow-500/20 hover:bg-yellow-500/20 text-yellow-500 border border-yellow-500/30 font-extrabold rounded-md">
                                M:{sevCounts.medium}
                              </Badge>
                            )}
                            {sevCounts.low > 0 && (
                              <Badge className="h-4 px-1 text-[8px] bg-blue-500/20 hover:bg-blue-500/20 text-blue-500 border border-blue-500/30 font-extrabold rounded-md">
                                L:{sevCounts.low}
                              </Badge>
                            )}
                            {sevCounts.info > 0 && (
                              <Badge className="h-4 px-1 text-[8px] bg-slate-500/20 hover:bg-slate-500/20 text-slate-400 border border-slate-500/30 font-extrabold rounded-md">
                                I:{sevCounts.info}
                              </Badge>
                            )}
                            {sevCounts.critical === 0 && sevCounts.high === 0 && sevCounts.medium === 0 && sevCounts.low === 0 && sevCounts.info === 0 && (
                              <span className="text-[10px] font-black text-emerald-500 uppercase tracking-wider">
                                Secure
                              </span>
                            )}
                          </div>
                        </div>
                        <div>
                          <p className="text-[10px] text-muted-foreground">Share</p>
                          <p className="font-black text-sm">{info.percentage.toFixed(1)}%</p>
                        </div>
                      </div>
                      {info.findings.length > 0 ? (
                        expandedLang === lang ? <ChevronUp className="w-4 h-4 text-muted-foreground" /> : <ChevronDown className="w-4 h-4 text-muted-foreground" />
                      ) : null}
                    </button>

                    {expandedLang === lang && (
                      <div className="border-t border-border/50">
                        {info.findings.length === 0 ? (
                          <div className="p-5 flex items-center gap-2 text-muted-foreground/60">
                            <Shield className="w-4 h-4" />
                            <span className="text-xs font-semibold">No security findings for this language</span>
                          </div>
                        ) : (
                          <div className="overflow-y-auto p-5 space-y-3 max-h-[520px]">
                            {info.findings.map((f: any, fi: number) => {
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
                );
              })}
            </div>
          </div>
        ) : (
          /* Developer split view: Report vs Insights */
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 h-[550px] animate-in fade-in duration-300">
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
        )}
      </div>
    );
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[100vw] w-screen h-screen m-0 p-0 overflow-hidden border-none bg-background flex flex-col rounded-none">

        {/* Top Header */}
        <DialogHeader className="px-8 py-5 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
          <div className="flex items-center gap-4">
            <div className="p-3 rounded-2xl bg-violet-500/10 border border-violet-500/20 text-violet-500 shrink-0">
              <ShieldCheck className="w-7 h-7" />
            </div>
            <div>
              <DialogTitle className="text-xl font-display font-black tracking-tight uppercase leading-none">
                Quick Security Scanner
              </DialogTitle>
              <DialogDescription className="text-[10px] font-bold text-muted-foreground uppercase tracking-[0.25em] flex items-center gap-2 mt-1.5">
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

        {/* Two-Column Grid Body */}
        <div className="flex-1 overflow-y-auto p-6 sm:p-10 w-full max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-8 items-start">

            {/* Left Column (Input & Status Stepper) */}
            <div className="lg:sticky lg:top-0 flex flex-col gap-6">
              <div className="rounded-[2rem] border border-border/50 bg-background/50 backdrop-blur-sm p-8 flex flex-col gap-5 shadow-xl shadow-primary/5">
                <div className="flex flex-col gap-4">
                  <div className="flex items-center justify-between text-muted-foreground">
                    <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block">
                      Source Ingestion
                    </label>
                    <Badge variant="outline" className="rounded-md font-mono text-[9px] font-bold px-2 py-0">
                      {detectLanguage(code).toUpperCase()}
                    </Badge>
                  </div>

                  <div className="relative h-[300px] rounded-2xl bg-card border border-border/50 overflow-hidden focus-within:ring-2 focus-within:ring-violet-500/25 transition-all">
                    <textarea
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      className="w-full h-full bg-transparent p-5 font-mono text-xs focus:outline-none resize-none leading-relaxed focus:ring-0"
                      spellCheck="false"
                      placeholder="# Paste code here..."
                      disabled={isScanning}
                    />
                  </div>

                  <Button
                    onClick={runScan}
                    disabled={isScanning || !code.trim()}
                    className="h-12 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-bold text-sm flex items-center justify-center gap-2 shadow-lg shadow-violet-600/20 transition-all active:scale-[0.98]"
                  >
                    {isScanning ? (
                      <>
                        <LoadingSpinner size="sm" className="text-current" />
                        <span>Scanning...</span>
                      </>
                    ) : (
                      <>
                        <Zap className="w-4 h-4 fill-current" />
                        EXECUTE AUDIT
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {selectedJob && (
                <div className="rounded-[2rem] border border-border/50 bg-background/50 backdrop-blur-sm p-8 flex flex-col gap-4 shadow-xl shadow-primary/5 animate-in fade-in slide-in-from-top-3 duration-300">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">Pipeline Status</label>
                  <div className="flex flex-col gap-1">
                    {QUICK_SCAN_STEPS.map((step, i) => {
                      const stepIdx = QUICK_SCAN_STEPS.findIndex(s => s.key === selectedJob.status);
                      const isError = selectedJob.status === "ERROR";
                      const isDone = selectedJob.status === "DONE" ? true : i < stepIdx;
                      const isActive = step.key === selectedJob.status && !isError;
                      return (
                        <div key={step.key} className={cn("flex items-center gap-3 py-2.5 px-3 rounded-xl transition-all", isActive ? "bg-violet-500/8" : "")}>
                          <div className={cn("w-6 h-6 rounded-full flex items-center justify-center shrink-0 border-2 transition-all",
                            isError && i >= stepIdx ? "border-destructive/30 text-destructive/30" :
                              isDone ? "border-emerald-500 bg-emerald-500/10 text-emerald-500" :
                                isActive ? "border-violet-500 bg-violet-500/10 text-violet-500" :
                                  "border-border text-muted-foreground/30")}>
                            {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <span>{i + 1}</span>}
                          </div>
                          <span className={cn("text-xs font-semibold", isDone ? "text-emerald-500" : isActive ? "text-foreground" : "text-muted-foreground/40")}>
                            {step.label}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  <div className="h-1.5 rounded-full bg-muted/50 overflow-hidden">
                    <div className={cn("h-full rounded-full transition-all duration-700 ease-out", selectedJob.status === "ERROR" ? "bg-destructive" : "bg-violet-500")} style={{ width: `${selectedJob.progress}%` }} />
                  </div>
                  {selectedJob.stepMessage && <p className="text-[11px] text-muted-foreground">{selectedJob.stepMessage}</p>}
                </div>
              )}
            </div>

            {/* Right Column (Results & Telemetry Stream) */}
            <div className="min-h-[500px]">
              {/* Ready state */}
              {!selectedJob && (
                <div className="h-[480px] rounded-[2rem] border border-dashed border-border/50 flex flex-col items-center justify-center text-center gap-5 bg-muted/5">
                  <ShieldCheck className="w-12 h-12 text-muted-foreground/30 animate-pulse" />
                  <div>
                    <p className="font-black uppercase tracking-widest text-sm text-foreground">Ready to Scan</p>
                    <p className="text-xs text-muted-foreground mt-1.5">Input your code snippet on the left and click Execute Audit to begin security validation.</p>
                  </div>
                </div>
              )}

              {/* Ingress / Scanning active state */}
              {selectedJob && selectedJob.status !== "DONE" && selectedJob.status !== "ERROR" && (
                <div className="max-w-5xl mx-auto w-full">
                  <UnifiedPipelineView
                    job={selectedJob}
                    steps={QUICK_SCAN_STEPS}
                    result={selectedResult}
                    onResultRender={renderQuickScanResult}
                    onCancel={handleCancelJob}
                  />
                </div>
              )}

              {/* Error state */}
              {selectedJob && selectedJob.status === "ERROR" && (
                <div className="rounded-[2rem] border-2 border-destructive/20 bg-destructive/5 p-10 flex flex-col gap-6">
                  <div className="flex items-center gap-4 text-destructive">
                    <AlertCircle className="w-10 h-10" />
                    <h2 className="text-2xl font-black tracking-tight">Scan Failed</h2>
                  </div>
                  <p className="font-mono text-sm text-destructive/80 bg-black/5 rounded-2xl p-6 border border-destructive/10 leading-relaxed">
                    {selectedJob.stepMessage || "An unexpected error occurred during sandbox execution."}
                  </p>
                </div>
              )}

              {/* Finished / Done state */}
              {selectedJob && selectedJob.status === "DONE" && selectedResult && (
                <div className="max-w-5xl mx-auto w-full">
                  {renderQuickScanResult(selectedResult)}
                </div>
              )}
            </div>

          </div>
        </div>

      </DialogContent>
    </Dialog>
  );
};

export default SecurityScanner;
