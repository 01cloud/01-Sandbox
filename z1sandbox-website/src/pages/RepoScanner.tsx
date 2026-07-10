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
import ThemeToggle from "@/components/ThemeToggle";
import UserSettingsDialog from "@/components/UserSettingsDialog";
import { Settings } from "lucide-react";

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

const GIT_URL_PATTERN = /^(https?:\/\/|git@|ssh:\/\/)([a-zA-Z0-9\-.]+)(:|\/)([A-Za-z0-9_.\-]+)\/([A-Za-z0-9_.\-]+?)(\.git)?\/?$/;

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
  if (import.meta.env.DEV) return "";
  const explicit = (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL;
  if (explicit) return explicit;
  try {
    const backendsRaw = (window as any)._env_?.VITE_DASHBOARD_BACKENDS_JSON
      || import.meta.env.VITE_DASHBOARD_BACKENDS_JSON;
    if (backendsRaw) {
      const backends = JSON.parse(backendsRaw);
      if (Array.isArray(backends) && backends.length > 0) {
        const parsed = new URL(backends[0].baseUrl);
        if (parsed.origin !== window.location.origin) return parsed.origin;
      }
    }
  } catch { /* local dev — fall through */ }
  return "";
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
  const [scanningLanguages, setScanningLanguages] = useState<Record<string, string>>({});

  // Private repository scan states
  const [requiresAuth, setRequiresAuth] = useState(false);
  const [authMethod, setAuthMethod] = useState<"token" | "ssh">("token");
  const [gitToken, setGitToken] = useState("");
  const [sshKey, setSshKey] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => { esRef.current?.close(); }, []);

  const validateUrl = (url: string) => {
    if (!url) { setUrlError(""); return; }
    const urls = url.split(/[\s,;\n]+/).map(u => u.trim()).filter(Boolean);
    const invalid = urls.filter(u => !GIT_URL_PATTERN.test(u));
    if (invalid.length > 0) {
      setUrlError(`Invalid URL(s): ${invalid.slice(0, 2).join(", ")}${invalid.length > 2 ? "..." : ""}`);
    } else {
      setUrlError("");
    }
  };

  const checkPrivateRepo = async (url: string) => {
    const trimmed = url.trim();
    if (!trimmed || !GIT_URL_PATTERN.test(trimmed)) {
      setRequiresAuth(false);
      return;
    }

    const apiKey = getApiKey();
    if (!apiKey) return;

    setIsValidating(true);
    try {
      const resp = await fetch(`${API_BASE}/v1/repo-scan/precheck`, {
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

  const resetScan = () => {
    setCurrentStep(""); setStepMessage(""); setProgress(0); setResult(null); setExpandedLang(null);
    setActiveJobId(null);
    setScanningLanguages({});
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

        if (ev.step === "SCANNING" && ev.detail && ev.detail.languages) {
          setScanningLanguages(ev.detail.languages);
        }

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
   * Surfaces CLI-triggered scans (active OR already-completed) in the UI.
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

        if (!serverJobs.length) return;

        // If we are currently scanning, viewing a result, or have an active job, do not auto-switch
        if (activeJobId || result || isScanning) return;

        // 1. Prefer an active job — stream live events
        const active = serverJobs.find(
          j => !["DONE", "ERROR"].includes(j.status)
        );
        if (active && active.job_id !== activeJobId) {
          toast.info("CLI scan detected — connecting to live stream...");
          connectStream(active.job_id, apiKey, active.eventIndex ?? 0);
          return;
        }

        // 2. If no active job, check for a completed job not yet shown
        const latest = serverJobs[0]; // already sorted newest-first by backend
        if (!latest || latest.job_id === activeJobId || result) return;

        if (latest.status === "DONE") {
          // Fetch the stored result directly — no need to open an SSE stream
          const res = await fetch(`${API_BASE}/v1/repo-scan/${latest.job_id}/result`, {
            headers: { Authorization: `Bearer ${apiKey}` },
          });
          if (res.ok) {
            const data = await res.json();
            setActiveJobId(latest.job_id);
            setCurrentStep("DONE");
            setProgress(100);
            setStepMessage("Scan completed");
            setResult(data);
            setIsScanning(false);
            toast.info("CLI scan result loaded.");
          }
        } else if (latest.status === "ERROR") {
          // Replay the error event via the SSE stream (returns immediately from Redis)
          toast.info("CLI scan detected (failed) — replaying error...");
          connectStream(latest.job_id, apiKey, 0);
        }
      } catch { /* silent */ }
    };

    poll(); // immediate on mount
    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API_BASE, activeJobId, result]);

  const handleScan = async () => {
    const urls = repoUrl
      .split(/[\s,;\n]+/)
      .map((u) => u.trim())
      .filter((u) => u.length > 0);

    if (urls.length === 0) {
      setUrlError("Enter a valid repository URL");
      return;
    }

    const invalidUrls = urls.filter(u => !GIT_URL_PATTERN.test(u));
    if (invalidUrls.length > 0) {
      setUrlError(`Invalid repository URL(s): ${invalidUrls.join(", ")}`);
      return;
    }

    const apiKey = getApiKey();
    if (!apiKey) {
      toast.error("No API key found. Create one in the Dashboard → API Management tab.");
      return;
    }

    if (!requiresAuth) {
      setIsValidating(true);
      try {
        const resp = await fetch(`${API_BASE}/v1/repo-scan/precheck`, {
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

    resetScan();
    setIsScanning(true);

    try {
      let successCount = 0;
      let firstJobId = "";

      for (const url of urls) {
        try {
          const bodyPayload: any = { repo_url: url };
          if (requiresAuth) {
            if (authMethod === "token" && gitToken.trim()) {
              bodyPayload.git_token = gitToken.trim();
            } else if (authMethod === "ssh" && sshKey.trim()) {
              bodyPayload.ssh_key = sshKey.trim();
            }
          }

          const resp = await fetch(`${API_BASE}/v1/repo-scan`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
            body: JSON.stringify(bodyPayload),
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || `Failed to start scan for ${url}`);

          if (!firstJobId) {
            firstJobId = data.job_id;
          }
          successCount++;
        } catch (err: any) {
          console.error(`Failed to scan ${url}:`, err);
        }
      }

      if (successCount > 0) {
        toast.success(`Successfully started ${successCount} repository scan(s)! You can track all of them on the Dashboard.`);
        setRepoUrl(""); // Clear input on success
        setRequiresAuth(false);
        setGitToken("");
        setSshKey("");
        if (firstJobId) {
          connectStream(firstJobId, apiKey, 0);
        }
      } else {
        throw new Error("Failed to start repository scans.");
      }
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
        <div className="flex items-center justify-between mb-6">
          <button
            onClick={() => navigate("/dashboard")}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors group"
          >
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
            Back to Dashboard
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsSettingsOpen(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-border/50 bg-background/50 hover:bg-muted/65 text-xs font-bold text-muted-foreground hover:text-foreground transition-all group"
            >
              <Settings className="w-3.5 h-3.5 group-hover:rotate-45 transition-transform duration-300" />
              Settings
            </button>
            <ThemeToggle />
          </div>
        </div>

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
              <div className="flex items-center justify-between mb-2">
                <label htmlFor="repo-url-input" className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block">
                  Repository URL
                </label>
                {isValidating && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />}
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
                placeholder="https://github.com/owner/repo1, repo2..."
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

            {requiresAuth && (
              <div className="border-t border-violet-500/20 pt-5 flex flex-col gap-4 animate-in fade-in slide-in-from-top-2 duration-300">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-violet-500 font-bold text-xs uppercase tracking-wider">
                    <Shield className="w-4 h-4" />
                    Private Repo Detected
                  </div>
                  <div className="flex bg-muted/30 rounded-lg p-0.5">
                    <button
                      type="button"
                      onClick={() => setAuthMethod("token")}
                      className={cn(
                        "px-3 py-1 text-[10px] font-black uppercase rounded-md transition-all",
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
                        "px-3 py-1 text-[10px] font-black uppercase rounded-md transition-all",
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
                    <label htmlFor="git-token-input" className="text-[9px] font-black uppercase tracking-wider text-muted-foreground">
                      Personal Access Token (PAT)
                    </label>
                    <Input
                      id="git-token-input"
                      type="password"
                      value={gitToken}
                      onChange={(e) => setGitToken(e.target.value)}
                      placeholder="ghp_xxxxxxxxxxxx or GitLab/Bitbucket token"
                      className="rounded-xl h-10 font-mono text-xs border-border/50 bg-background/50 focus:border-violet-500/50"
                    />
                    <p className="text-[10px] text-muted-foreground/60 leading-normal">
                      Token is not stored. It will be used ephemerally to authenticate the clone process and then discarded.
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="ssh-key-input" className="text-[9px] font-black uppercase tracking-wider text-muted-foreground">
                      SSH Private Key
                    </label>
                    <textarea
                      id="ssh-key-input"
                      value={sshKey}
                      onChange={(e) => setSshKey(e.target.value)}
                      placeholder="Paste your SSH Private Key here..."
                      className="rounded-xl min-h-[120px] p-3 font-mono text-xs border border-border/50 bg-background/50 focus:border-violet-500/50 focus:outline-none focus:ring-1 focus:ring-violet-500/50 resize-y"
                    />
                    <p className="text-[10px] text-muted-foreground/60 leading-normal">
                      Paste the private deploy key with read permissions. This is processed entirely in memory and never stored in the database.
                    </p>
                  </div>
                )}
              </div>
            )}

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
            <div className="h-[480px] rounded-[2rem] border border-violet-500/20 bg-violet-500/5 flex flex-col items-center justify-center p-8 gap-6 overflow-hidden">
              {currentStep === "SCANNING" && Object.keys(scanningLanguages).length > 0 ? (
                <div className="w-full max-w-md flex flex-col gap-6">
                  <div className="text-center">
                    <Loader2 className="w-10 h-10 animate-spin text-violet-500 mx-auto mb-3" />
                    <p className="font-black text-xl tracking-tight">Security Scan In Progress</p>
                    <p className="text-xs text-muted-foreground mt-1.5">{stepMessage || "Analyzing files in isolated sandboxes..."}</p>
                  </div>

                  <div className="rounded-2xl border border-border/50 bg-background/50 p-5 flex flex-col gap-3 shadow-md max-h-[260px] overflow-y-auto">
                    <p className="text-[10px] font-black uppercase tracking-wider text-muted-foreground mb-1">Language Pipeline</p>
                    {Object.entries(scanningLanguages).map(([lang, status]) => {
                      const isPending = status === "PENDING";
                      const isScanningLang = status === "SCANNING";
                      const isDone = status === "DONE";
                      const isFailed = status === "FAILED";

                      return (
                        <div key={lang} className="flex items-center justify-between py-1.5 border-b border-border/20 last:border-0">
                          <span className="font-bold text-sm">{lang}</span>
                          <Badge
                            variant="outline"
                            className={cn(
                              "text-[10px] font-bold px-2 py-0.5 flex items-center gap-1.5 uppercase",
                              isPending && "bg-muted/30 text-muted-foreground border-border",
                              isScanningLang && "bg-violet-500/10 text-violet-500 border-violet-500/30",
                              isDone && "bg-emerald-500/10 text-emerald-500 border-emerald-500/30",
                              isFailed && "bg-destructive/10 text-destructive border-destructive/30"
                            )}
                          >
                            {isScanningLang && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
                            {isDone && <CheckCircle2 className="w-2.5 h-2.5" />}
                            {isFailed && <AlertCircle className="w-2.5 h-2.5" />}
                            {status === "PENDING" ? "Pending" : status === "SCANNING" ? "Scanning" : status === "DONE" ? "Complete" : "Failed"}
                          </Badge>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <>
                  <Loader2 className="w-10 h-10 animate-spin text-violet-500" />
                  <p className="font-black text-xl tracking-tight">{STEP_LABELS[currentStep] || "Processing..."}</p>
                  {stepMessage && <p className="text-xs text-muted-foreground -mt-3">{stepMessage}</p>}
                </>
              )}
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
              <div className="p-1 flex flex-wrap items-center gap-6">
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
                </div>
              </div>

              {chartData.length > 0 && (
                <div className="border-t border-border/60 pt-6 p-1 space-y-4">
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground mb-2">Language Distribution</h3>
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

              <div className="border-t border-border/60 pt-6 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
                    Per-Language Details
                  </h3>
                  <span className="text-[10px] text-muted-foreground/60 font-bold">
                    {langEntries.length} languages analyzed
                  </span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
                    const isSecure = sevCounts.critical === 0 && sevCounts.high === 0 && sevCounts.medium === 0 && sevCounts.low === 0 && sevCounts.info === 0;

                    return (
                      <div key={lang} className="rounded-2xl border border-border bg-muted/10 hover:bg-muted/20 transition-all">
                        <button
                          className="w-full p-5 flex items-center justify-between text-left"
                          onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                        >
                          <div className="flex items-center gap-3">
                            <span className="w-3 h-3 rounded-full shrink-0" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                            <span className="font-black text-sm text-foreground">{lang}</span>
                          </div>
                          <div className="flex items-center gap-3 text-right">
                            <div className="flex flex-col items-end gap-1">
                              <div className="text-[10px] font-bold text-foreground/80">
                                {info.percentage.toFixed(1)}%
                              </div>
                              <div className="flex gap-1 items-center">
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
                                {isSecure && (
                                  <Badge className="h-4.5 px-1.5 text-[8px] bg-emerald-500/10 hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold rounded-md">
                                    SECURE
                                  </Badge>
                                )}
                              </div>
                            </div>

                          </div>
                        </button>

                        {expandedLang === lang && (
                          <div className="border-t border-border/50">
                            {info.findings.length === 0 ? (
                              <div className="p-5 text-xs font-semibold text-muted-foreground/60 flex items-center gap-2">
                                <Shield className="w-4 h-4" />
                                <span>No security findings for this language</span>
                              </div>
                            ) : (
                              <div className="overflow-y-auto p-4 space-y-2.5 max-h-[420px]">
                                {info.findings.map((f: any, fi: number) => {
                                  const sev = f.severity?.toUpperCase() ?? "INFO";
                                  const sevColor =
                                    sev === "CRITICAL" ? "border-red-500/30 bg-red-500/[0.03] text-red-900 dark:text-red-200" :
                                    sev === "HIGH" ? "border-orange-500/30 bg-orange-500/[0.03] text-orange-900 dark:text-orange-200" :
                                    sev === "MEDIUM" ? "border-yellow-500/30 bg-yellow-500/[0.03] text-yellow-900 dark:text-yellow-200" :
                                    sev === "LOW" ? "border-blue-500/30 bg-blue-500/[0.03] text-blue-900 dark:text-blue-200" :
                                                    "border-border/30 bg-muted/[0.02] text-foreground";
                                  const badgeColor =
                                    sev === "CRITICAL" ? "bg-red-500/10 text-red-600 dark:text-red-400" :
                                    sev === "HIGH" ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" :
                                    sev === "MEDIUM" ? "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400" :
                                    sev === "LOW" ? "bg-blue-500/10 text-blue-600 dark:text-blue-400" :
                                                    "bg-muted/30 text-muted-foreground";
                                  return (
                                    <div key={fi} className={cn("p-3 rounded-xl border text-[11px] transition-all flex flex-col gap-1.5", sevColor)}>
                                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                                        <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide px-1.5 py-0 border-0 rounded-md", badgeColor)}>
                                          {sev}
                                        </Badge>
                                        <span className="text-[10px] font-bold text-muted-foreground">{f.tool}</span>
                                        {f.line && (
                                          <span className="text-[9px] font-mono text-muted-foreground/50 ml-auto">L:{f.line}</span>
                                        )}
                                      </div>
                                      <p className="font-semibold text-foreground/90 leading-snug">{f.issue}</p>
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
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      <UserSettingsDialog isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  );
}
