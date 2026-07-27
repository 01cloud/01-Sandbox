import { useState, useEffect } from "react";
import { Github, Search, CheckCircle2, AlertCircle, Loader2, X, Activity, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "@/components/ui/resizable";

import { useJobStore } from "@/hooks/useJobStore";
import { JobsPanel } from "./JobsPanel";
import { UnifiedPipelineView } from "./UnifiedPipelineView";
import { InlineApiKeyPanel } from "./InlineApiKeyPanel";

interface RepoScannerWidgetProps {
  apiBaseUrl: string;
  keys: { id: string; backend: string }[];
  authToken?: string;
  inline?: boolean;
  onSwitchTab?: (tab: string) => void;
  backendId?: string;
}

const REPO_SCAN_STEPS = [
  { key: "QUEUED", label: "Job Queued" },
  { key: "PROVISIONING", label: "Provisioning sandbox environment" },
  { key: "CLONING", label: "Cloning repository" },
  { key: "DETECTING", label: "Detecting languages" },
  { key: "SCANNING", label: "Running security scan" },
  { key: "DONE", label: "Scan complete" },
];

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

const GITHUB_PATTERN = /^https:\/\/github\.com\/[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+\/?$/;

export default function RepoScannerWidget({ apiBaseUrl, keys, authToken, inline = false, onSwitchTab, backendId = "Z1_SANDBOX" }: RepoScannerWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);

  const [requiresAuth, setRequiresAuth] = useState(false);
  const [authMethod, setAuthMethod] = useState<"token" | "ssh">("token");
  const [gitToken, setGitToken] = useState("");
  const [sshKey, setSshKey] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [runtime, setRuntime] = useState<string>("gvisor");

  const getApiKey = () => {
    for (const k of keys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) return saved;
    }
    return null;
  };

  const hasKeyInDb = keys.length > 0;
  const apiKey = getApiKey() || (hasKeyInDb ? authToken : "") || "";

  // Initialize unified hook
  const {
    jobs,
    volatileResults,
    volatileLogs,
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
    const urls = url.split(/[\s,;\n]+/).map(u => u.trim()).filter(Boolean);
    const invalid = urls.filter(u => !GITHUB_PATTERN.test(u));
    if (invalid.length > 0) {
      setUrlError(`Invalid URL(s): ${invalid.slice(0, 2).join(", ")}${invalid.length > 2 ? "..." : ""}`);
    } else {
      setUrlError("");
    }
  };

  const checkPrivateRepo = async (url: string) => {
    const trimmed = url.trim();
    if (!trimmed || !GITHUB_PATTERN.test(trimmed)) {
      setRequiresAuth(false);
      return;
    }

    if (!apiKey) return;

    setIsValidating(true);
    try {
      const resp = await fetch(`${apiBaseUrl}/v1/repo-scan/precheck`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({ repo_url: trimmed }),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.requires_auth) {
          setRequiresAuth(true);
          if (trimmed.startsWith("git@") || trimmed.startsWith("ssh://")) {
            setAuthMethod("ssh");
          } else {
            setAuthMethod("token");
          }
        } else {
          setRequiresAuth(false);
        }
      }
    } catch (err) {
      console.error("Precheck failed:", err);
    } finally {
      setIsValidating(false);
    }
  };

  const handleUrlBlur = () => {
    validateUrl(repoUrl);
    if (!urlError && repoUrl.trim()) {
      checkPrivateRepo(repoUrl);
    }
  };

  const handleScan = async () => {
    const urls = repoUrl
      .split(/[\s,;\n]+/)
      .map((u) => u.trim())
      .filter((u) => u.length > 0);

    if (urls.length === 0) {
      setUrlError("Enter a valid public GitHub URL");
      return;
    }

    const invalidUrls = urls.filter(u => !GITHUB_PATTERN.test(u));
    if (invalidUrls.length > 0) {
      setUrlError(`Invalid GitHub URL(s): ${invalidUrls.join(", ")}`);
      return;
    }

    if (!apiKey) {
      toast.error("No API key found. Please create one in API Management tab."); return;
    }

    if (!requiresAuth) {
      setIsValidating(true);
      try {
        const resp = await fetch(`${apiBaseUrl}/v1/repo-scan/precheck`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiKey}`,
          },
          body: JSON.stringify({ repo_url: urls[0] }),
        });
        if (resp.ok) {
          const data = await resp.json();
          if (data.requires_auth) {
            setRequiresAuth(true);
            if (urls[0].startsWith("git@") || urls[0].startsWith("ssh://")) {
              setAuthMethod("ssh");
            } else {
              setAuthMethod("token");
            }
            toast.error("This repository requires authentication. Please provide a Personal Access Token or SSH Deploy Key below.");
            setIsValidating(false);
            return;
          }
        }
      } catch (err) {
        console.error("Precheck failed during submission:", err);
      } finally {
        setIsValidating(false);
      }
    }

    try {
      setIsScanning(true);
      let successCount = 0;
      let failCount = 0;
      let lastJobId = "";

      for (const url of urls) {
        try {
          const bodyPayload: any = { repo_url: url, backend_id: backendId, runtime: runtime };
          if (requiresAuth) {
            if (authMethod === "token" && gitToken.trim()) {
              bodyPayload.git_token = gitToken.trim();
            } else if (authMethod === "ssh" && sshKey.trim()) {
              bodyPayload.ssh_key = sshKey.trim();
            }
          }

          const resp = await fetch(`${apiBaseUrl}/v1/repo-scan`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
            body: JSON.stringify(bodyPayload),
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || "Failed to start scan");

          const { job_id, repo_url: data_repo_url, submitted_at } = data;
          lastJobId = job_id;

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

          openStream(job_id, 0);
          successCount++;
        } catch (err: any) {
          console.error(`Failed to trigger scan for ${url}:`, err);
          failCount++;
        }
      }

      if (lastJobId) {
        setSelectedJobId(lastJobId);
      }

      // Force immediate sync
      syncFromServer();

      if (failCount === 0) {
        toast.success(`Successfully initiated ${successCount} repository scan(s)!`);
        setRepoUrl(""); // Clear input on complete success
        setRequiresAuth(false);
        setGitToken("");
        setSshKey("");
      } else if (successCount > 0) {
        toast.warning(`Initiated ${successCount} scan(s), but ${failCount} failed.`);
      } else {
        toast.error("Failed to initiate repository scans.");
      }
    } catch (err: any) {
      toast.error(err.message || "Failed to start scan");
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
      const response = await fetch(`${apiBaseUrl}/v1/jobs/${jobId}`, {
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
  // Use volatile RAM result first (freshly fetched), fall back to job.detail only if job is DONE and contains full scan report.
  const isSelectedDone = selectedJob?.status === "DONE" || selectedJob?.status === "COMPLETE";
  const selectedResult = (selectedJobId && isSelectedDone ? volatileResults[selectedJobId] : null) ?? (
    isSelectedDone && selectedJob?.detail && selectedJob.detail.total_findings !== undefined
      ? selectedJob.detail
      : null
  );

  // Custom renderer for scan result findings matching Quick Scanner design
  const renderRepoScanResult = (result: any) => {
    if (!result) return null;
    const owner = result.owner || (result.repo_url ? result.repo_url.split("github.com/")[1]?.split("/")[0] : "") || "Repository";
    const repo = result.repo || (result.repo_url ? result.repo_url.split("/").pop() : "") || "Scan";
    const totalFindings = result.total_findings ?? 0;
    const totalFiles = result.total_files ?? 0;
    const duration = result.scan_duration_seconds ?? 0;
    const detectionTool = result.detection_tool || "OpenSandbox";

    const langEntries = result && result.languages ? Object.entries(result.languages) : [];
    const chartData = langEntries.map(([lang, r]: [string, any]) => ({
      name: lang, value: r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
    }));

    return (
      <div className="space-y-6 mt-4 animate-in fade-in duration-500">
        {/* ── Verdict Banner (Matching Quick Scanner Image 2) ── */}
        <div className="p-1 flex flex-wrap items-center gap-4">
          <div className={cn(
            "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border",
            totalFindings === 0
              ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-500"
              : "bg-red-500/10 border-red-500/20 text-red-500"
          )}>
            {totalFindings === 0 ? (
              <CheckCircle2 className="w-5 h-5" />
            ) : (
              <AlertCircle className="w-5 h-5" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-black text-base text-foreground">
              {totalFindings === 0 ? "SCAN VERDICT: SECURE" : `VULNERABILITIES DETECTED (${totalFindings})`}
            </p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Detected by <span className="font-semibold text-violet-500">Unified Ingestion Pipeline</span>
              {" · "}{owner}/{repo}{" · "}{totalFiles} files scanned{" · "}{duration}s
            </p>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Badge variant="outline" className="bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/25 font-bold text-xs px-3 py-1">
              {langEntries.length} {langEntries.length === 1 ? "Language" : "Languages"}
            </Badge>
            <Badge variant="outline" className={cn("font-bold text-xs px-3 py-1", totalFindings === 0 ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/25" : "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/25")}>
              {totalFindings} Findings
            </Badge>
          </div>
        </div>

        {/* ── Language Distribution ── */}
        {chartData.length > 0 && (
          <div className="border-t border-border/60 pt-6 p-1 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
                Language Distribution
              </h3>
              <span className="text-[10px] text-muted-foreground/60">
                by % of codebase
              </span>
            </div>

            {/* Thinner stacked horizontal color bar */}
            <div className="h-1.5 rounded-full overflow-hidden flex border border-border/10 bg-muted/20">
              {chartData.map((d, i) => (
                <div
                  key={d.name}
                  className="h-full transition-all duration-500 hover:brightness-110 relative group"
                  style={{
                    width: `${Math.max(d.value, 1)}%`,
                    backgroundColor: LANG_COLORS[i % LANG_COLORS.length],
                  }}
                  title={`${d.name}: ${d.value}%`}
                >
                  <div className="absolute -top-8 left-1/2 -translate-x-1/2 bg-background border border-border/50 rounded-md px-2 py-0.5 text-[9px] font-bold opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-10 shadow-lg pointer-events-none">
                    {d.name}: {d.value}%
                  </div>
                </div>
              ))}
            </div>

            {/* Grid legend list with file count and percentage (no individual bars) */}
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 pt-1">
              {chartData.map((d, i) => {
                const langInfo = result?.languages ? (result.languages as any)[d.name] : null;
                return (
                  <div key={d.name} className="flex items-center justify-between text-xs py-1 border-b border-border/10">
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: LANG_COLORS[i % LANG_COLORS.length] }}
                      />
                      <span className="font-bold text-foreground/90 truncate">{d.name}</span>
                    </div>
                    <div className="flex items-center gap-2 text-right shrink-0">
                      <span className="font-extrabold text-foreground/80 tabular-nums">{d.value}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Per-Language Details ── */}
        <div className="border-t border-border/60 pt-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
              Per-Language Details
            </h3>
            <span className="text-[10px] text-muted-foreground/60">
              {langEntries.length} languages analyzed
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {langEntries.map(([lang, info]: [string, any], i) => {
              const findingsList = Array.isArray(info?.findings) ? info.findings : [];
              const sevCounts = findingsList.reduce(
                (acc: any, f: any) => {
                  const sev = (f.severity || "INFO").toUpperCase();
                  if (sev === "CRITICAL") acc.critical = (acc.critical || 0) + 1;
                  else if (sev === "HIGH") acc.high = (acc.high || 0) + 1;
                  else if (sev === "MEDIUM") acc.medium = (acc.medium || 0) + 1;
                  else if (sev === "LOW") acc.low = (acc.low || 0) + 1;
                  else if (sev === "INFO") acc.info = (acc.info || 0) + 1;
                  return acc;
                },
                { critical: 0, high: 0, medium: 0, low: 0, info: 0 }
              );
              const isSecure = sevCounts.critical === 0 && sevCounts.high === 0 && sevCounts.medium === 0 && sevCounts.low === 0 && sevCounts.info === 0;

              return (
                <div key={lang} className="rounded-2xl border border-border bg-muted/10 hover:bg-muted/20 transition-all">
                  <button
                    className="w-full p-5 flex items-center justify-between text-left"
                    onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className="w-3 h-3 rounded-full shrink-0"
                        style={{ background: LANG_COLORS[i % LANG_COLORS.length] }}
                      />
                      <span className="font-black text-sm text-foreground">{lang}</span>
                    </div>
                    <div className="flex items-center gap-3 text-right">
                      <div className="flex flex-col items-end gap-1">
                        <div className="text-[10px] font-bold text-foreground/80">
                          {info.percentage?.toFixed(1)}%
                        </div>
                        <div className="flex gap-1 items-center">
                          {isSecure ? (
                            <Badge className="h-4.5 px-1.5 text-[8px] bg-emerald-500/10 hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold rounded-md">
                              SECURE
                            </Badge>
                          ) : (
                            <>
                              {sevCounts.critical > 0 && (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-red-500/10 hover:bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20 font-extrabold rounded-md">
                                  C:{sevCounts.critical}
                                </Badge>
                              )}
                              {sevCounts.high > 0 && (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-orange-500/10 hover:bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/20 font-extrabold rounded-md">
                                  H:{sevCounts.high}
                                </Badge>
                              )}
                              {sevCounts.medium > 0 && (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-yellow-500/10 hover:bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/20 font-extrabold rounded-md">
                                  M:{sevCounts.medium}
                                </Badge>
                              )}
                              {sevCounts.low > 0 && (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-blue-500/10 hover:bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20 font-extrabold rounded-md">
                                  L:{sevCounts.low}
                                </Badge>
                              )}
                              {sevCounts.info > 0 && (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-slate-500/10 hover:bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20 font-extrabold rounded-md">
                                  I:{sevCounts.info}
                                </Badge>
                              )}
                            </>
                          )}
                        </div>
                      </div>

                    </div>
                  </button>
                  {expandedLang === lang && (
                    <div className="border-t border-border/50">
                      {findingsList.length === 0 ? (
                        <div className="p-5 text-xs font-semibold text-muted-foreground/60">
                          No security findings for this language
                        </div>
                      ) : (
                        <div className="overflow-y-auto p-4 space-y-2.5 max-h-[420px]">
                          {findingsList.map((f: any, fi: number) => {
                            const sev = f.severity?.toUpperCase() ?? "INFO";
                            const sevColor =
                              sev === "CRITICAL" ? "border-red-500/30 bg-red-500/[0.03] text-red-900 dark:text-red-200" :
                              sev === "HIGH"     ? "border-orange-500/30 bg-orange-500/[0.03] text-orange-900 dark:text-orange-200" :
                              sev === "MEDIUM"   ? "border-yellow-500/30 bg-yellow-500/[0.03] text-yellow-900 dark:text-yellow-200" :
                              sev === "LOW"      ? "border-blue-500/30 bg-blue-500/[0.03] text-blue-900 dark:text-blue-200" :
                                                   "border-border/30 bg-muted/[0.02] text-foreground";
                            const badgeColor =
                              sev === "CRITICAL" ? "bg-red-500/10 text-red-600 dark:text-red-400" :
                              sev === "HIGH"     ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" :
                              sev === "MEDIUM"   ? "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400" :
                              sev === "LOW"      ? "bg-blue-500/10 text-blue-600 dark:text-blue-400" :
                                                   "bg-muted/30 text-muted-foreground";
                            const rawPath = f.file || "";
                            const cleanPath = rawPath.includes("/workspace/")
                              ? rawPath.split("/workspace/")[1]
                              : rawPath.replace(/^\.\//, "");
                            const fileName = cleanPath ? cleanPath.split("/").pop() : "";

                            return (
                              <div key={fi} className={cn("p-3 rounded-xl border text-[11px] transition-all flex flex-col gap-1.5", sevColor)}>
                                <div className="flex items-center gap-2 flex-wrap">
                                  <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide px-1.5 py-0 border-0 rounded-md", badgeColor)}>
                                    {sev}
                                  </Badge>
                                  <span className="text-[10px] font-bold text-muted-foreground">
                                    {f.tool} {fileName ? `(${fileName})` : ""}
                                  </span>
                                  {f.line && (
                                    <span className="text-[9px] font-mono font-bold text-muted-foreground/80 ml-auto bg-background/60 px-1.5 py-0.5 rounded border border-border/30">
                                      L:{f.line}
                                    </span>
                                  )}
                                </div>
                                <p className="font-semibold text-foreground/90 leading-snug">{f.issue}</p>
                                {cleanPath && (
                                  <div className="flex items-center gap-1.5 mt-1">
                                    <p className="text-[9px] font-mono font-medium text-muted-foreground/70 bg-background/40 px-2 py-0.5 rounded border border-border/20 truncate" title={cleanPath}>
                                      📄 {cleanPath}
                                    </p>
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
              );
            })}
          </div>
        </div>
      </div>
    );
  };

  if (inline) {
    return (
      <div className="w-full h-[calc(100vh-210px)] min-h-[600px] border-t border-b border-border flex flex-row overflow-hidden p-0 animate-in fade-in duration-500">
        {/* Left Sidebar: Input + Scans */}
        <div className="w-full md:w-[26%] shrink-0 border-r border-border flex flex-col h-full overflow-hidden p-4 space-y-4">
          {/* Box 1: REPOSITORY URL Input & Button */}
          <div className="flex flex-col gap-4 shrink-0 px-1 py-2">
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label htmlFor="repo-url-input-inline" className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                  Repository URL
                </label>
                {isValidating && <Loader2 className="w-3 h-3 animate-spin text-violet-500" />}
              </div>
              <Input
                id="repo-url-input-inline"
                value={repoUrl}
                onChange={(e) => {
                  setRepoUrl(e.target.value);
                  validateUrl(e.target.value);
                  if (!e.target.value) {
                    setRequiresAuth(false);
                    setGitToken("");
                    setSshKey("");
                  }
                }}
                onBlur={handleUrlBlur}
                placeholder="https://github.com/owner/repo"
                className={cn("rounded-lg h-10 font-mono text-xs border border-border bg-background text-foreground placeholder:text-muted-foreground/45 transition-colors focus:border-violet-500/40 focus:ring-1 focus:ring-violet-500/20", urlError ? "border-red-500/50" : "")}
                disabled={isScanning}
              />
              {urlError && <p className="text-[10px] text-red-500 font-medium">{urlError}</p>}
            </div>

            {/* Isolation Runtime Selector */}
            <div className="flex flex-col gap-2 shrink-0">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                Isolation Runtime
              </label>
              <div className="flex bg-muted/40 rounded-xl p-0.5 border border-border/50">
                <button
                  type="button"
                  onClick={() => setRuntime("gvisor")}
                  className={cn(
                    "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
                    runtime === "gvisor"
                      ? "bg-violet-600 text-white shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <Shield className="w-3 h-3" />
                  gVisor
                </button>
                <button
                  type="button"
                  onClick={() => setRuntime("kata-fc")}
                  className={cn(
                    "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
                    runtime === "kata-fc"
                      ? "bg-violet-600 text-white shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <Activity className="w-3 h-3" />
                  Kata-FC
                </button>
              </div>
            </div>

            {requiresAuth && (
              <div className="border-t border-violet-500/25 pt-4 flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-300">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-violet-500 font-bold text-[10px] uppercase tracking-wider">
                    <Shield className="w-3.5 h-3.5" />
                    Private Repo
                  </div>
                  <div className="flex bg-muted/30 rounded-md p-0.5">
                    <button
                      type="button"
                      onClick={() => setAuthMethod("token")}
                      className={cn(
                        "px-2.5 py-1 text-[9px] font-black uppercase rounded transition-all",
                        authMethod === "token"
                          ? "bg-violet-600 text-white shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      Token
                    </button>
                    <button
                      type="button"
                      onClick={() => setAuthMethod("ssh")}
                      className={cn(
                        "px-2.5 py-1 text-[9px] font-black uppercase rounded transition-all",
                        authMethod === "ssh"
                          ? "bg-violet-600 text-white shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      SSH Key
                    </button>
                  </div>
                </div>

                {authMethod === "token" ? (
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="git-token-input-inline" className="text-[8px] font-black uppercase tracking-wider text-muted-foreground/60">
                      Personal Access Token (PAT)
                    </label>
                    <Input
                      id="git-token-input-inline"
                      type="password"
                      value={gitToken}
                      onChange={(e) => setGitToken(e.target.value)}
                      placeholder="ghp_xxxxxxxxxxxx"
                      className="rounded-lg h-9 font-mono text-[11px] border border-border bg-background text-foreground/90 focus:border-violet-500/40"
                    />
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="ssh-key-input-inline" className="text-[8px] font-black uppercase tracking-wider text-muted-foreground/60">
                      SSH Private Key
                    </label>
                    <textarea
                      id="ssh-key-input-inline"
                      value={sshKey}
                      onChange={(e) => setSshKey(e.target.value)}
                      placeholder="Paste your SSH Private Key here..."
                      className="rounded-lg min-h-[100px] p-3 font-mono text-[11px] border border-border bg-background text-foreground/90 focus:border-violet-500/40 focus:outline-none focus:ring-1 focus:ring-violet-500/20 resize-y"
                    />
                  </div>
                )}
              </div>
            )}

            <Button
              id="scan-repo-btn-inline"
              onClick={handleScan}
              disabled={isScanning || !!urlError || !repoUrl}
              className="w-full h-9 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-bold text-[11px] flex items-center justify-center gap-1.5 shadow-md shadow-violet-600/10 transition-all disabled:opacity-40 disabled:shadow-none shrink-0 uppercase tracking-wider"
            >
              {isScanning ? <><Loader2 className="w-3 h-3 animate-spin" /> Ingesting...</> : <><Search className="w-3 h-3" /> Scan Repository</>}
            </Button>
          </div>

          <div className="flex items-center justify-between px-1 shrink-0">
            <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">
              Repository Scans
            </span>
            <span className="text-[9px] text-muted-foreground/60 font-semibold tracking-wider uppercase">
              Recent ({jobs.length})
            </span>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            <JobsPanel
              jobs={jobs}
              selectedJobId={selectedJobId}
              onSelectJob={setSelectedJobId}
              onDeleteJob={removeJob}
              jobType="repo-scan"
              embedded={true}
            />
          </div>
        </div>

        {/* Right Panel: Pipeline + Results */}
        <div className="flex-grow flex flex-col h-full overflow-hidden bg-background">
          <ScrollArea className="flex-grow">
            <div className="w-full p-4">
              {selectedJob ? (
                <UnifiedPipelineView
                  job={selectedJob}
                  steps={REPO_SCAN_STEPS}
                  result={selectedResult}
                  onResultRender={renderRepoScanResult}
                  onCancel={handleCancelJob}
                  logs={selectedJobId ? volatileLogs[selectedJobId] : undefined}
                />
              ) : (
                <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
                  <div className="w-16 h-16 rounded-2xl bg-violet-500/5 border border-violet-500/10 flex items-center justify-center">
                    <Activity className="w-7 h-7 text-violet-500/30" />
                  </div>
                  <div>
                    <h3 className="text-sm font-black uppercase tracking-wider text-muted-foreground/45">No Scan Selected</h3>
                    <p className="text-xs text-muted-foreground/35 mt-1">
                      Select a repository scan job from the panel or run a new scan.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>
    );
  }

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
            onClick={() => {
              if (onSwitchTab) {
                onSwitchTab("scanner");
              } else {
                setIsOpen(true);
              }
            }}
          >
            <Search className="w-4 h-4" />
            Scan Repository
          </Button>
        </CardContent>
      </Card>

      {/* Full Scanner Dialog */}
      <Dialog open={isOpen} onOpenChange={(o) => { if (!o) { setSelectedJobId(null); setRequiresAuth(false); setGitToken(""); setSshKey(""); } setIsOpen(o); }}>
        <DialogContent className="max-w-[1240px] w-[95vw] h-[90vh] rounded-3xl border border-border bg-background flex flex-col overflow-hidden p-0 shadow-2xl">

          {/* ── Header ── */}
          <DialogHeader className="px-8 py-4 border-b border-border bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl bg-violet-500/10 border border-violet-500/20 text-violet-500 shadow-[0_0_20px_rgba(139,92,246,0.1)]">
                <Github className="w-5 h-5" />
              </div>
              <div>
                <DialogTitle className="text-2xl font-display font-black tracking-tight text-foreground">GitHub Repository Scanner</DialogTitle>
              </div>
            </div>
          </DialogHeader>

          {/* ── Two-Column Layout ── */}
          <div className="flex-1 flex overflow-hidden">

            {/* Left Sidebar: Input + Scans */}
            <div className="w-[360px] shrink-0 flex flex-col h-full overflow-hidden p-4 space-y-4">

              {/* Box 1: REPOSITORY URL Input & Button */}
              <div className="rounded-[1.25rem] border border-border bg-card p-4 flex flex-col gap-4 shadow-sm shrink-0">
                {/* URL Input */}
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <label htmlFor="repo-url-input" className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                      Repository URL
                    </label>
                    {isValidating && <Loader2 className="w-3 h-3 animate-spin text-violet-500" />}
                  </div>
                  <Input
                    id="repo-url-input"
                    value={repoUrl}
                    onChange={(e) => {
                      setRepoUrl(e.target.value);
                      validateUrl(e.target.value);
                      if (!e.target.value) {
                        setRequiresAuth(false);
                        setGitToken("");
                        setSshKey("");
                      }
                    }}
                    onBlur={handleUrlBlur}
                    placeholder="https://github.com/owner/repo"
                    className={cn("rounded-lg h-10 font-mono text-xs border border-border bg-background text-foreground placeholder:text-muted-foreground/45 transition-colors focus:border-violet-500/40 focus:ring-1 focus:ring-violet-500/20", urlError ? "border-red-500/50" : "")}
                    disabled={isScanning}
                  />
                  {urlError && <p className="text-[10px] text-red-500 font-medium">{urlError}</p>}
                </div>

                {/* Isolation Runtime Selector */}
                <div className="flex flex-col gap-2 shrink-0">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                    Isolation Runtime
                  </label>
                  <div className="flex bg-muted/40 rounded-xl p-0.5 border border-border/50">
                    <button
                      type="button"
                      onClick={() => setRuntime("gvisor")}
                      className={cn(
                        "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
                        runtime === "gvisor"
                          ? "bg-violet-600 text-white shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <Shield className="w-3 h-3" />
                      gVisor
                    </button>
                    <button
                      type="button"
                      onClick={() => setRuntime("kata-fc")}
                      className={cn(
                        "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
                        runtime === "kata-fc"
                      ? "bg-violet-600 text-white shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <Activity className="w-3 h-3" />
                      Kata-FC
                    </button>
                  </div>
                </div>

                {/* Auth Panel */}
                {requiresAuth && (
                  <div className="rounded-xl border border-violet-500/15 bg-violet-500/5 p-4 flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-300">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 text-violet-500 font-bold text-[10px] uppercase tracking-wider">
                        <Shield className="w-3.5 h-3.5" />
                        Private Repo
                      </div>
                      <div className="flex bg-muted/40 rounded-md p-0.5 border border-border/50">
                        <button
                          type="button"
                          onClick={() => setAuthMethod("token")}
                          className={cn(
                            "px-2.5 py-1 text-[9px] font-black uppercase rounded transition-all",
                            authMethod === "token"
                              ? "bg-violet-600 text-white shadow-sm"
                              : "text-muted-foreground hover:text-foreground"
                          )}
                        >
                          Token
                        </button>
                        <button
                          type="button"
                          onClick={() => setAuthMethod("ssh")}
                          className={cn(
                            "px-2.5 py-1 text-[9px] font-black uppercase rounded transition-all",
                            authMethod === "ssh"
                              ? "bg-violet-600 text-white shadow-sm"
                              : "text-muted-foreground hover:text-foreground"
                          )}
                        >
                          SSH Key
                        </button>
                      </div>
                    </div>

                    {authMethod === "token" ? (
                      <div className="flex flex-col gap-1.5">
                        <label htmlFor="git-token-input" className="text-[8px] font-black uppercase tracking-wider text-muted-foreground/60">
                          Personal Access Token (PAT)
                        </label>
                        <Input
                          id="git-token-input"
                          type="password"
                          value={gitToken}
                          onChange={(e) => setGitToken(e.target.value)}
                          placeholder="ghp_xxxxxxxxxxxx"
                          className="rounded-lg h-9 font-mono text-[11px] border border-border bg-background text-foreground/90 focus:border-violet-500/40"
                        />
                        <p className="text-[9px] text-muted-foreground/45 leading-normal">
                          Token is used ephemerally and never stored.
                        </p>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-1.5">
                        <label htmlFor="ssh-key-input" className="text-[8px] font-black uppercase tracking-wider text-muted-foreground/60">
                          SSH Private Key
                        </label>
                        <textarea
                          id="ssh-key-input"
                          value={sshKey}
                          onChange={(e) => setSshKey(e.target.value)}
                          placeholder="Paste your SSH Private Key here..."
                          className="rounded-lg min-h-[100px] p-3 font-mono text-[11px] border border-border bg-background text-foreground/90 focus:border-violet-500/40 focus:outline-none focus:ring-1 focus:ring-violet-500/20 resize-y"
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* Scan Button - Purple Solid matching Image 2 */}
                <Button
                  id="scan-repo-btn"
                  onClick={handleScan}
                  disabled={isScanning || !!urlError || !repoUrl}
                  className="w-full h-9 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-bold text-[11px] flex items-center justify-center gap-1.5 shadow-md shadow-violet-600/10 transition-all disabled:opacity-40 disabled:shadow-none shrink-0 uppercase tracking-wider"
                >
                  {isScanning ? <><Loader2 className="w-3 h-3 animate-spin" /> Ingesting...</> : <><Search className="w-3 h-3" /> Scan Repository</>}
                </Button>
              </div>

              {/* Title & Stats */}
              <div className="flex items-center justify-between px-1 shrink-0">
                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">
                  Repository Scans
                </span>
                <span className="text-[9px] text-muted-foreground/60 font-semibold tracking-wider uppercase">
                  Recent ({jobs.length})
                </span>
              </div>

              {/* Box 2: Scrollable Scan List */}
              <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
                <JobsPanel
                  jobs={jobs}
                  selectedJobId={selectedJobId}
                  onSelectJob={setSelectedJobId}
                  onDeleteJob={removeJob}
                  jobType="repo-scan"
                  embedded={true}
                />
              </div>
            </div>

            {/* Right Panel: Pipeline + Results */}
            <div className="flex-1 bg-background overflow-hidden flex flex-col">
              <ScrollArea className="flex-1">
                <div className="w-full p-6">
                  {selectedJob ? (
                    <UnifiedPipelineView
                      job={selectedJob}
                      steps={REPO_SCAN_STEPS}
                      result={selectedResult}
                      onResultRender={renderRepoScanResult}
                      onCancel={handleCancelJob}
                      logs={selectedJobId ? volatileLogs[selectedJobId] : undefined}
                    />
                  ) : (
                    <div className="h-[60vh] flex flex-col items-center justify-center text-center gap-4">
                      <div className="w-16 h-16 rounded-2xl bg-violet-500/5 border border-violet-500/10 flex items-center justify-center">
                        <Activity className="w-7 h-7 text-violet-500/30" />
                      </div>
                      <div>
                        <h3 className="text-sm font-black uppercase tracking-wider text-muted-foreground/45">No Scan Selected</h3>
                        <p className="text-xs text-muted-foreground/35 mt-1">
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
