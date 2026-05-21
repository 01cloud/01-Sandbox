import { useAuth0 } from "@auth0/auth0-react";
import { useEffect, useState } from "react";
import DOMPurify from 'dompurify';
import {
  Plus,
  Trash2,
  ExternalLink,
  ShieldCheck,
  Terminal,
  Key,
  LayoutDashboard,
  Box,
  ChevronRight,
  RefreshCw,
  Copy,
  CheckCircle2,
  ExternalLink as ExternalLinkIcon,
  Search,
  Code,
  Clock,
  Calendar,
  UploadCloud,
  Play,
  FileCode
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import SecurityScanner from "@/components/dashboard/SecurityScanner";

interface APIKey {
  id: string;
  name: string;
  backend: string;
  prefix: string;
  created_at: string;
  expires_at: string;
  is_revoked: boolean;
}

const Dashboard = () => {
  const API_BASE_URL = (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "";
  const { user, getAccessTokenSilently, isAuthenticated, isLoading: authLoading } = useAuth0();
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<{ id: string; key: string; status?: string } | null>(null);
  const [form, setForm] = useState({ name: "", backend: "Z1_SANDBOX", ttl: "never", ttlValue: "1" });
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);

  // --- DEVELOPER TESTING MODE STATES & FUNCTIONS ---
  const [devMode, setDevMode] = useState(false);
  const [bulkQueue, setBulkQueue] = useState<{ name: string; content: string; lang: string }[]>([]);
  const [isBulkScanning, setIsBulkScanning] = useState(false);
  const [bulkScanLogs, setBulkScanLogs] = useState<{
    name: string;
    status: 'idle' | 'scanning' | 'clean' | 'risks' | '429' | '401' | 'error';
    findingsCount?: number;
    duration?: number;
    errorMsg?: string;
  }[]>([]);
  const [rateLimitCountdown, setRateLimitCountdown] = useState<number | null>(null);

  const processFiles = async (fileList: FileList) => {
    const newItems: { name: string; content: string; lang: string }[] = [];

    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];
      const content = await file.text();
      const ext = file.name.split('.').pop()?.toLowerCase() || '';

      // If it is a YAML file, check if it contains multiple documents separated by ---
      if ((ext === 'yaml' || ext === 'yml') && content.includes('---')) {
        const parts = content.split('---').map(p => p.trim()).filter(p => p.length > 0);
        if (parts.length > 1) {
          parts.forEach((part, index) => {
            newItems.push({
              name: `${file.name.replace(/\.(yaml|yml)$/, '')}_doc_${index + 1}.yaml`,
              content: part,
              lang: 'k8s'
            });
          });
          continue;
        }
      }

      // If it is a TXT file, parse custom bulk delimiter blocks (==== lang: <language> ====)
      if (ext === 'txt' && content.includes('==== lang:')) {
        const blocks = content.split(/====\s*lang:\s*([a-zA-Z0-9_-]+)\s*====/i);
        for (let j = 1; j < blocks.length; j += 2) {
          const blockLang = blocks[j].toLowerCase();
          const blockContent = blocks[j + 1]?.trim();
          if (blockContent && blockContent.length > 0) {
            newItems.push({
              name: `bulk_${file.name.replace('.txt', '')}_${Math.floor(Math.random() * 1000)}_${j}.${blockLang === 'k8s' ? 'yaml' : blockLang}`,
              content: blockContent,
              lang: blockLang
            });
          }
        }
        continue;
      }

      // Auto-detect language based on extension
      let lang = 'py';
      if (ext === 'yaml' || ext === 'yml') lang = 'yaml';
      else if (ext === 'go') lang = 'go';
      else if (ext === 'js' || ext === 'ts') lang = 'js';
      else if (ext === 'sh') lang = 'sh';

      newItems.push({
        name: file.name,
        content,
        lang
      });
    }

    setBulkQueue(prev => [...prev, ...newItems]);
    setBulkScanLogs(prev => [
      ...prev,
      ...newItems.map(item => ({ name: item.name, status: 'idle' as const }))
    ]);
    toast.success(`Successfully queued ${newItems.length} scan targets!`);
  };

  const runBulkSecurityAudit = async (backend: string, baseUrl: string) => {
    if (bulkQueue.length === 0) {
      toast.error("Please upload files first");
      return;
    }

    // Find active developer API key
    const backendKeys = keys.filter(k => k.backend === backend);
    let foundKey = "";
    for (const k of backendKeys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) {
        foundKey = saved;
        break;
      }
    }

    if (!foundKey) {
      toast.error(`No locally saved API Key found for ${backend}. Please create one in the API Management tab.`);
      return;
    }

    setIsBulkScanning(true);

    // Process sequential queue
    for (let i = 0; i < bulkQueue.length; i++) {
      const item = bulkQueue[i];

      // Update status to scanning
      setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: 'scanning' } : log));

      let attemptScan = true;
      while (attemptScan) {
        try {
          const apiExt = item.lang === 'k8s' ? 'yaml' : item.lang;
          const filename = item.name.includes('.') ? item.name : `${item.name}.${apiExt}`;

          const response = await fetch(`${baseUrl}/scan-jobs`, {
            method: "POST",
            headers: {
              "accept": "application/json",
              "Content-Type": "application/json",
              "Authorization": `Bearer ${foundKey}`
            },
            body: JSON.stringify({
              files: { [filename]: item.content }
            })
          });

          const data = await response.json();

          if (response.status === 429) {
            // Rate Limit hit!
            const retryAfter = data.detail?.retry_after || data.retry_after || 60;
            toast.warning(`Rate limit hit. Waiting ${retryAfter}s before retrying...`);

            // Wait for retry duration
            setRateLimitCountdown(retryAfter);
            setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: '429', errorMsg: `Rate limit hit. Retrying in ${retryAfter}s...` } : log));

            for (let sec = retryAfter; sec > 0; sec--) {
              setRateLimitCountdown(sec);
              await new Promise(resolve => setTimeout(resolve, 1000));
            }
            setRateLimitCountdown(null);
            // Retries the current item loop without moving forward
            continue;
          }

          if (!response.ok) {
            const errStatus = response.status === 401 ? '401' : 'error';
            const errMsg = data.detail || data.error || "Ingestion error";
            setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: errStatus, errorMsg: errMsg } : log));
            attemptScan = false;
            break;
          }

          const report = data.report || data;
          const totalFindings = report.findings?.length || report.summary?.findings_count || 0;
          const finalStatus = totalFindings > 0 ? 'risks' : 'clean';

          setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? {
            ...log,
            status: finalStatus,
            findingsCount: totalFindings,
            duration: 0.8
          } : log));

          attemptScan = false;
        } catch (error: any) {
          console.error("Bulk scan error:", error);
          setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: 'error', errorMsg: error.message } : log));
          attemptScan = false;
        }
      }

      // Enforce the mandatory 2-second delay between sequential scans
      if (i < bulkQueue.length - 1) {
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }

    setIsBulkScanning(false);
    toast.success("Bulk security audit finished!");
  };

  const [scannerConfig, setScannerConfig] = useState<{
    isOpen: boolean;
    backend: string;
    baseUrl: string;
    apiKey: string;
  }>({
    isOpen: false,
    backend: "Z1_SANDBOX",
    baseUrl: "",
    apiKey: ""
  });

  const [backends, setBackends] = useState<any[]>([]);

  const fetchBackends = async () => {
    try {
      const envBackendsJson = (window as any)._env_?.VITE_DASHBOARD_BACKENDS_JSON || import.meta.env.VITE_DASHBOARD_BACKENDS_JSON;

      if (envBackendsJson) {
        let rawJson = typeof envBackendsJson === 'string' ? envBackendsJson.trim() : envBackendsJson;
        // Strip leading/trailing single quotes if they exist
        if (typeof rawJson === 'string' && rawJson.startsWith("'") && rawJson.endsWith("'")) {
          rawJson = rawJson.substring(1, rawJson.length - 1);
        }

        const data = typeof rawJson === 'string' ? JSON.parse(rawJson) : rawJson;

        // Process backends to ensure they have all required fields dynamically
        const processed = data.map((b: any) => {
          // If a backend misses a baseUrl, dynamically compose one relative to 'v1'
          const defaultBase = b.baseUrl || `/api/v1/${b.id.toLowerCase()}`;
          return {
            ...b,
            baseUrl: defaultBase,
            documentationUrl: b.documentationUrl || `${defaultBase}/docs`
          };
        });
        setBackends(processed);
        return;
      }

      // Default fallback if no env is set
      setBackends([]);
    } catch (e) {
      console.error("Failed to load backends config from environment:", e);
      setBackends([]);
    }
  };



  const fetchKeys = async () => {
    try {
      setIsLoading(true);
      const token = await getAccessTokenSilently();
      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await response.json();
      if (data.keys) {
        setKeys(data.keys.filter((k: APIKey) => !k.is_revoked));
      }
    } catch (error) {
      console.error("Error fetching keys:", error);
      toast.error("Failed to fetch API keys");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchBackends();
    if (isAuthenticated) {
      fetchKeys();
    }
  }, [isAuthenticated]);

  const handleCreateKey = async () => {
    // 1. Sanitize using lightweight DOMPurify to strip any HTML/Script tags
    const sanitizedName = DOMPurify.sanitize(form.name).trim();

    if (!sanitizedName) {
      toast.error("Please enter a valid key name");
      return;
    }

    // 2. Enforce strict character whitelist
    const safeNameRegex = /^[a-zA-Z0-9\s\-_]+$/;
    if (!safeNameRegex.test(sanitizedName)) {
      toast.error("Invalid key name. Only alphanumeric characters, spaces, hyphens and underscores are allowed.");
      return;
    }

    try {
      setIsCreating(true);
      const token = await getAccessTokenSilently();

      let ttl_hours = -1;
      const val = parseInt(form.ttlValue);
      if (form.ttl === "minutes") ttl_hours = val / 60;
      else if (form.ttl === "hours") ttl_hours = val;
      else if (form.ttl === "days") ttl_hours = val * 24;
      else if (form.ttl === "months") ttl_hours = val * 24 * 30;
      else if (form.ttl === "years") ttl_hours = val * 24 * 365;

      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: sanitizedName,
          backend: form.backend,
          ttl_hours,
          user_email: user?.email,
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Failed to create key");

      setNewKey({ id: data.api_key_id, key: data.api_key, status: data.status });
      localStorage.setItem(`bound_key_${data.api_key_id}`, data.api_key);
      fetchKeys();
      toast.success("API Key generated successfully!");
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevokeKey = async (id: string) => {
    try {
      const token = await getAccessTokenSilently();
      await fetch(`${API_BASE_URL}/v1/api-keys/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      setKeys(keys.filter((k) => k.id !== id));
      toast.info("Key revoked successfully");
    } catch (error) {
      toast.error("Failed to revoke key");
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard");
  };

  const handleQuickScan = (backend: string, baseUrl: string) => {
    // Find the latest active key for this backend from localStorage
    const backendKeys = keys.filter(k => k.backend === backend);
    let foundKey = "";

    for (const k of backendKeys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) {
        foundKey = saved;
        break;
      }
    }

    if (!foundKey) {
      toast.error(`No locally saved API Key found for ${backend}. Please create one or ensure it's in this browser's storage.`);
      return;
    }

    setScannerConfig({
      isOpen: true,
      backend,
      baseUrl,
      apiKey: foundKey
    });
  };

  const bindAndVisit = async (backend: string, url: string) => {
    const backendKeys = keys.filter(k => k.backend === backend);
    let foundKey = "";

    for (const k of backendKeys) {
      const saved = localStorage.getItem(`bound_key_${k.id}`);
      if (saved) {
        foundKey = saved;
        break;
      }
    }

    if (!foundKey) {
      toast.error(`No locally saved API Key found for ${backend}. Please create one in the API Management tab.`);
      return;
    }
    // Bind both execution token and management token for Swagger
    const token = await getAccessTokenSilently();
    document.cookie = `inspector_auth=${token}; SameSite=Lax; Path=/; Max-Age=${60 * 60 * 24}`;
    document.cookie = `execution_token=${foundKey}; SameSite=Lax; Path=/; Max-Age=${60 * 60 * 24 * 7}`;

    window.open(url, '_blank');
  };

  if (authLoading) return (
    <div className="min-h-screen pt-32 flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <RefreshCw className="w-8 h-8 animate-spin text-primary" />
        <p className="text-muted-foreground font-medium">Verifying security credentials...</p>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen pt-32 pb-20 px-6 sm:px-8 max-w-7xl mx-auto">
      <header className="mb-12">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
            <LayoutDashboard className="w-6 h-6" />
          </div>
          <h1 className="text-4xl font-display font-black tracking-tight">Developer Dashboard</h1>
        </div>
        <p className="text-muted-foreground text-lg max-w-2xl">
          Securely manage your API integrations, track sandbox activity, and scale your intelligence infrastructure.
        </p>
      </header>

      <Tabs defaultValue="apps" className="space-y-8">
        <TabsList className="bg-secondary/30 p-1.5 rounded-2xl border border-border/50 h-auto gap-1 flex-nowrap overflow-x-auto no-scrollbar justify-start sm:justify-center">
          <TabsTrigger value="apps" className="rounded-xl px-6 py-2.5 data-[state=active]:bg-background data-[state=active]:shadow-sm font-semibold flex items-center gap-2 whitespace-nowrap">
            <Box className="w-4 h-4" />
            Applications
          </TabsTrigger>
          <TabsTrigger value="apis" className="rounded-xl px-6 py-2.5 data-[state=active]:bg-background data-[state=active]:shadow-sm font-semibold flex items-center gap-2 whitespace-nowrap">
            <Key className="w-4 h-4" />
            API Management
          </TabsTrigger>
        </TabsList>

        <TabsContent value="apps" className="animate-in fade-in-50 slide-in-from-bottom-5 duration-500">
          <div className="mb-8 flex items-center justify-between p-6 rounded-[2rem] bg-secondary/15 border border-border/40 backdrop-blur-sm shadow-xl shadow-primary/5 transition-all">
            <div>
              <h3 className="text-lg font-black tracking-tight flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse shadow-[0_0_8px_rgba(99,102,241,0.6)]" />
                Developer Ingestion & Testing Mode
              </h3>
              <p className="text-sm text-muted-foreground mt-1">Unlock raw file batching, automatic K8s YAML multi-document parsing, and bulk cooldowned automated testing.</p>
            </div>
            <button
              onClick={() => {
                setDevMode(!devMode);
                setBulkQueue([]);
                setBulkScanLogs([]);
              }}
              className={cn(
                "relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-300 ease-in-out focus:outline-none bg-zinc-800",
                devMode ? "bg-indigo-600 shadow-[0_0_12px_rgba(99,102,241,0.4)]" : "bg-zinc-800"
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-300 ease-in-out mt-0.5",
                  devMode ? "translate-x-5" : "translate-x-0.5"
                )}
              />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {backends.map((app) => {
              const IconComponent = app.icon === "terminal" ? Terminal : (app.icon === "box" ? Box : Code);
              const colorClass = app.color === "indigo" ? "bg-indigo-500/10 text-indigo-500 border-indigo-500/20" : "bg-emerald-500/10 text-emerald-500 border-emerald-500/20";

              return (
                <Card key={app.id} className="group relative overflow-hidden rounded-[2rem] border-border/50 bg-background/50 backdrop-blur-sm transition-all hover:border-primary/50 hover:shadow-2xl hover:shadow-primary/5">
                  <CardHeader className="p-8 pb-4">
                    <div className="flex items-center gap-3 mb-4">
                      <div className={cn("p-3 rounded-2xl border", colorClass)}>
                        <IconComponent className="w-6 h-6" />
                      </div>
                      <CardTitle className="text-2xl font-black">{app.name}</CardTitle>
                    </div>
                    <CardDescription className="text-base text-muted-foreground">
                      {app.description}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="px-8 pb-8 flex flex-col gap-3 min-h-[140px] justify-end">
                    {app.baseUrl && (
                      <Button
                        className="w-full bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 rounded-2xl h-12 font-bold flex items-center gap-2 transition-all"
                        onClick={() => handleQuickScan(app.id, app.baseUrl)}
                      >
                        <Search className="w-4 h-4" />
                        Quick Scan
                      </Button>
                    )}
                    <Button
                      className="w-full bg-white/5 hover:bg-white/10 text-foreground border border-border/50 rounded-2xl h-12 font-bold flex items-center justify-center gap-2 transition-all"
                      onClick={() => bindAndVisit(app.id, app.documentationUrl)}
                    >
                      {app.id === "OPEN_SANDBOX" ? "Go to Application" : "View Documentation"}
                      <ExternalLinkIcon className="w-4 h-4 opacity-50" />
                    </Button>

                    {devMode && app.baseUrl && (
                      <div className="mt-6 pt-6 border-t border-border/40 flex flex-col gap-4 animate-in fade-in slide-in-from-top-3 duration-300">
                        <label className="text-[10px] font-black uppercase tracking-[0.2em] text-indigo-400">
                          RAW INGESTION ENGINE
                        </label>

                        <div
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            if (e.dataTransfer.files) {
                              processFiles(e.dataTransfer.files);
                            }
                          }}
                          onClick={() => document.getElementById(`dev-upload-${app.id}`)?.click()}
                          className="p-8 rounded-2xl border border-dashed border-border/70 hover:border-indigo-500/50 hover:bg-indigo-500/5 bg-secondary/5 flex flex-col items-center justify-center gap-3 cursor-pointer transition-all duration-300 relative group overflow-hidden"
                        >
                          <input
                            type="file"
                            multiple
                            id={`dev-upload-${app.id}`}
                            className="hidden"
                            onChange={(e) => {
                              if (e.target.files) {
                                processFiles(e.target.files);
                              }
                            }}
                          />
                          <UploadCloud className="w-10 h-10 text-muted-foreground group-hover:text-indigo-400 group-hover:scale-110 transition-all duration-300" />
                          <div className="text-center">
                            <p className="text-sm font-bold text-foreground">Drag & drop files or click to import</p>
                            <p className="text-[11px] text-muted-foreground mt-1">Supported: .yaml, .py, .go, .js, .sh</p>
                          </div>
                        </div>

                        {bulkQueue.length > 0 && (
                          <div className="flex flex-col gap-3">
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-bold text-muted-foreground">{bulkQueue.length} Targets Loaded</span>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-8 text-xs font-bold text-destructive hover:bg-destructive/10"
                                onClick={() => {
                                  setBulkQueue([]);
                                  setBulkScanLogs([]);
                                }}
                              >
                                Clear All
                              </Button>
                            </div>

                            <Button
                              onClick={() => runBulkSecurityAudit(app.id, app.baseUrl)}
                              disabled={isBulkScanning}
                              className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded-2xl h-11 font-bold flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/10 transition-all"
                            >
                              {isBulkScanning ? (
                                <>
                                  <RefreshCw className="w-4 h-4 animate-spin mr-2" />
                                  Scanning Queue... {rateLimitCountdown ? `[ Retry in ${rateLimitCountdown}s ]` : ''}
                                </>
                              ) : (
                                <>
                                  <Play className="w-4 h-4 fill-current mr-2" />
                                  Run Bulk Scan (2s Cooldown)
                                </>
                              )}
                            </Button>

                            {/* Telemetry Console widget */}
                            <div className="bg-zinc-950 rounded-2xl border border-white/5 p-4 max-h-[220px] overflow-y-auto custom-scrollbar font-mono text-[11px] leading-relaxed flex flex-col gap-2">
                              <div className="pb-2 border-b border-white/5 flex items-center justify-between text-[10px] text-muted-foreground">
                                <span>INGESTION STREAM</span>
                                <span>STATUS</span>
                              </div>
                              {bulkScanLogs.map((log, idx) => {
                                let statusIcon = "⚪";
                                let statusColor = "text-muted-foreground";
                                if (log.status === "scanning") {
                                  statusIcon = "🟡 Ingesting...";
                                  statusColor = "text-amber-400 animate-pulse";
                                } else if (log.status === "clean") {
                                  statusIcon = "✅ SECURE";
                                  statusColor = "text-emerald-400 font-bold";
                                } else if (log.status === "risks") {
                                  statusIcon = `🛑 VULN [${log.findingsCount || 0} risks]`;
                                  statusColor = "text-red-400 font-bold";
                                } else if (log.status === "429") {
                                  statusIcon = "⚠️ LIMIT (429)";
                                  statusColor = "text-yellow-500 font-bold animate-pulse";
                                } else if (log.status === "401") {
                                  statusIcon = "❌ BAD KEY (401)";
                                  statusColor = "text-rose-500 font-bold";
                                } else if (log.status === "error") {
                                  statusIcon = "❌ FAULT";
                                  statusColor = "text-rose-500 font-bold";
                                }

                                return (
                                  <div key={idx} className="flex items-center justify-between py-1 border-b border-white/5 last:border-0">
                                    <div className="flex items-center gap-2 truncate max-w-[65%]">
                                      <FileCode className="w-3.5 h-3.5 opacity-40 shrink-0" />
                                      <span className="truncate text-zinc-300">{log.name}</span>
                                    </div>
                                    <span className={cn("text-[10px] shrink-0 font-bold", statusColor)}>
                                      {statusIcon}
                                    </span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        <TabsContent value="apis" className="animate-in fade-in-50 slide-in-from-bottom-5 duration-500">
          <Card className="rounded-[2.5rem] border-border/50 bg-background/30 backdrop-blur-xl overflow-hidden">
            <CardHeader className="p-8 border-b border-border/50 flex flex-row items-center justify-between flex-wrap gap-4 bg-muted/20">
              <div>
                <CardTitle className="text-2xl font-black flex items-center gap-3">
                  Active Service Keys
                  <Badge variant="outline" className={`rounded-full px-3 py-1 text-xs font-bold font-mono ${keys.length >= 5 ? 'bg-destructive/5 text-destructive border-destructive/20' : 'bg-emerald-500/5 text-emerald-500 border-emerald-500/20'}`}>
                    {keys.length} / 5 KEYS USED
                  </Badge>
                </CardTitle>
                <CardDescription className="text-base mt-2">Manage your production and development access tokens.</CardDescription>
              </div>

              <Dialog onOpenChange={(open) => { if (!open) setNewKey(null); }}>
                <DialogTrigger asChild>
                  <Button className="rounded-2xl h-12 px-6 font-bold flex items-center gap-2 shadow-lg shadow-primary/10">
                    <Plus className="w-5 h-5" />
                    Generate New Key
                  </Button>
                </DialogTrigger>
                <DialogContent className="sm:max-w-md rounded-[2.5rem] border-border/50 p-8">
                  <DialogHeader className="mb-6">
                    <DialogTitle className="text-2xl font-black">Generate API Key</DialogTitle>
                    <DialogDescription className="text-base">
                      Assign a specific backend and TTL for your new security identity.
                    </DialogDescription>
                  </DialogHeader>

                  {newKey ? (
                    <div className="space-y-6">
                      <div className="p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-500 flex items-start gap-4">
                        <CheckCircle2 className="w-5 h-5 mt-0.5 shrink-0" />
                        <div className="text-sm font-medium">
                          {newKey.status || "Key generated successfully. Copy it now, as it won't be shown again."}
                        </div>
                      </div>
                      <div
                        className="group relative p-6 rounded-2xl bg-zinc-950 text-emerald-400 font-mono text-sm break-all cursor-pointer hover:bg-zinc-900 transition-colors border border-white/5"
                        onClick={() => copyToClipboard(newKey.key)}
                      >
                        {newKey.key}
                        <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity">
                          <Copy className="w-4 h-4 text-emerald-400/50" />
                        </div>
                      </div>
                      <Button className="w-full rounded-2xl h-12 font-bold" onClick={() => copyToClipboard(newKey.key)}>
                        Copy to Clipboard
                      </Button>
                    </div>
                  ) : (
                    <div className="space-y-6">
                      <div className="space-y-2">
                        <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Key Name</label>
                        <Input
                          placeholder="e.g. Production Scanner"
                          className="rounded-xl h-12 border-border/50 focus-visible:ring-primary/20"
                          value={form.name}
                          onChange={(e) => setForm({ ...form, name: e.target.value })}
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Target Backend</label>
                        <Select value={form.backend} onValueChange={(val) => setForm({ ...form, backend: val })}>
                          <SelectTrigger className="rounded-xl h-12 border-border/50">
                            <SelectValue placeholder="Select Backend" />
                          </SelectTrigger>
                          <SelectContent className="rounded-xl border-border/50">
                            <SelectItem value="Z1_SANDBOX">Z1 Sandbox</SelectItem>
                            <SelectItem value="OPEN_SANDBOX">OpenSandbox</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Time to Live</label>
                        <div className="flex gap-3">
                          <div className="flex-[0.4]">
                            <Input
                              type="number"
                              min="1"
                              className="rounded-xl h-12 border-border/50 focus-visible:ring-primary/20"
                              value={form.ttlValue}
                              onChange={(e) => setForm({ ...form, ttlValue: e.target.value })}
                              disabled={form.ttl === "never"}
                            />
                          </div>
                          <div className="flex-[0.6]">
                            <Select value={form.ttl} onValueChange={(val) => setForm({ ...form, ttl: val })}>
                              <SelectTrigger className="rounded-xl h-12 border-border/50">
                                <SelectValue placeholder="Unit" />
                              </SelectTrigger>
                              <SelectContent className="rounded-xl border-border/50">
                                <SelectItem value="minutes">Minutes</SelectItem>
                                <SelectItem value="hours">Hours</SelectItem>
                                <SelectItem value="days">Days</SelectItem>
                                <SelectItem value="months">Months</SelectItem>
                                <SelectItem value="years">Years</SelectItem>
                                <SelectItem value="never">Never Expire</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                        </div>
                      </div>
                      {keys.length >= 5 && (
                        <div className="p-4 rounded-2xl bg-destructive/5 border border-destructive/20 text-destructive text-sm font-bold flex items-center gap-3">
                          <ShieldCheck className="w-5 h-5" />
                          <span>You have reached the limit of 5 API keys. Please delete an existing key to create a new one.</span>
                        </div>
                      )}

                      <DialogFooter className="mt-8 pt-6 border-t border-border/50">
                        <Button
                          className="w-full rounded-2xl h-12 font-bold"
                          disabled={isCreating || keys.length >= 5}
                          onClick={handleCreateKey}
                        >
                          {isCreating ? (
                            <div className="flex items-center gap-2">
                              <LoadingSpinner size="sm" className="text-current" />
                              <span className="uppercase tracking-widest text-[10px]">Generating...</span>
                            </div>
                          ) : keys.length >= 5 ? (
                            "Limit Reached"
                          ) : (
                            "Generate Key"
                          )}
                        </Button>
                      </DialogFooter>
                    </div>
                  )}
                </DialogContent>
              </Dialog>
            </CardHeader>
            <CardContent className="p-0">
              {isLoading ? (
                <div className="p-20 flex flex-col items-center justify-center text-center space-y-6">
                  <LoadingSpinner className="text-primary/40" />
                  <p className="text-[10px] text-muted-foreground font-black uppercase tracking-[0.2em] opacity-60 animate-pulse">Synchronizing Tokens...</p>
                </div>
              ) : keys.length === 0 ? (
                <div className="p-20 flex flex-col items-center gap-6 text-center">
                  <div className="p-6 rounded-full bg-muted/10 text-muted-foreground border border-border/30">
                    <Key className="w-12 h-12 opacity-20" />
                  </div>
                  <div className="max-w-xs">
                    <p className="font-bold text-lg mb-1">No API keys found</p>
                    <p className="text-sm text-muted-foreground">Generate your first key to start interacting with the security backends.</p>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 p-2">
                  {keys.map((key) => (
                    <div
                      key={key.id}
                      className="group relative p-5 rounded-[2rem] bg-card/30 border border-border/40 hover:border-primary/30 transition-all duration-500 hover:shadow-2xl hover:shadow-primary/5 overflow-hidden"
                    >
                      <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />

                      <div className="relative flex flex-col lg:flex-row lg:items-center justify-between gap-6">
                        <div className="flex items-start gap-5 flex-1">
                          <div className="mt-1 p-3.5 rounded-2xl bg-primary/10 text-primary border border-primary/20 shadow-inner group-hover:scale-110 transition-transform duration-500">
                            <ShieldCheck className="w-6 h-6" />
                          </div>
                          <div className="space-y-2">
                            <div className="flex items-center gap-3">
                              <h3 className="font-display font-black text-xl tracking-tight">{key.name}</h3>
                              <Badge variant="outline" className="rounded-lg bg-primary/5 border-primary/20 text-[10px] font-black uppercase tracking-widest px-2 py-0.5">
                                {key.backend}
                              </Badge>
                            </div>
                            <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground/70">
                              <div className="flex items-center gap-1.5">
                                <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
                                <span className="font-mono">ID: {key.id.substring(0, 12)}...</span>
                              </div>
                              <div className="flex items-center gap-1.5">
                                <Clock className="w-3.5 h-3.5" />
                                <span>Created {new Date(key.created_at).toLocaleDateString()}</span>
                              </div>
                              <div className="flex items-center gap-1.5 ml-1 pl-4 border-l border-border/30">
                                <Calendar className="w-3.5 h-3.5" />
                                <span>
                                  {(() => {
                                    const exp = new Date(key.expires_at);
                                    const now = new Date();
                                    const diffMs = exp.getTime() - now.getTime();

                                    if (diffMs <= 0) return "Expired";

                                    const diffYears = exp.getFullYear() - now.getFullYear();
                                    if (diffYears > 50) return "Never Expires";

                                    const diffSeconds = Math.floor(diffMs / 1000);
                                    const diffMinutes = Math.floor(diffSeconds / 60);
                                    const diffHours = Math.floor(diffMinutes / 60);
                                    const diffDays = Math.floor(diffHours / 24);

                                    if (diffDays >= 365) {
                                      const years = Math.floor(diffDays / 365);
                                      return `Expires in ${years} year${years > 1 ? 's' : ''}`;
                                    }
                                    if (diffDays >= 30) {
                                      const months = Math.floor(diffDays / 30);
                                      return `Expires in ${months} month${months > 1 ? 's' : ''}`;
                                    }
                                    if (diffDays > 0) {
                                      return `Expires in ${diffDays} day${diffDays > 1 ? 's' : ''}`;
                                    }
                                    if (diffHours > 0) {
                                      return `Expires in ${diffHours} hour${diffHours > 1 ? 's' : ''}`;
                                    }
                                    if (diffMinutes > 0) {
                                      return `Expires in ${diffMinutes} minute${diffMinutes > 1 ? 's' : ''}`;
                                    }
                                    return "Expiring soon";
                                  })()}
                                </span>
                              </div>
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center">
                          <Button
                            variant="outline"
                            className="h-11 px-4 rounded-xl border-border/50 hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30 transition-all active:scale-95 flex items-center gap-2 font-bold text-xs uppercase tracking-wider"
                            onClick={() => setKeyToDelete(key.id)}
                          >
                            <Trash2 className="w-4 h-4" />
                            Delete
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
            <CardFooter className="p-8 border-t border-border/50 bg-muted/10">
              {/* <p className="text-xs text-muted-foreground font-medium flex items-center gap-2">
                <ShieldCheck className="w-3.5 h-3.5" />
                Your security keys are encrypted with AES-256-GCM. Never share your production keys.
              </p> */}
            </CardFooter>
          </Card>
        </TabsContent>
      </Tabs>

      <SecurityScanner
        isOpen={scannerConfig.isOpen}
        onClose={() => setScannerConfig(prev => ({ ...prev, isOpen: false }))}
        backend={scannerConfig.backend}
        baseUrl={scannerConfig.baseUrl}
        apiKey={scannerConfig.apiKey}
      />

      <AlertDialog open={!!keyToDelete} onOpenChange={(open) => !open && setKeyToDelete(null)}>
        <AlertDialogContent className="rounded-[2.5rem] border-border/50 p-8 bg-background/95 backdrop-blur-xl">
          <AlertDialogHeader>
            <div className="w-16 h-16 rounded-[2rem] bg-destructive/10 text-destructive flex items-center justify-center mb-6 mx-auto sm:mx-0">
              <Trash2 className="w-8 h-8" />
            </div>
            <AlertDialogTitle className="text-2xl font-black">Revoke API Key?</AlertDialogTitle>
            <AlertDialogDescription className="text-base text-muted-foreground">
              This action is permanent. Any systems or scripts currently using this key will immediately lose access to the security backends.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="mt-8 gap-3">
            <AlertDialogCancel className="rounded-2xl h-12 font-bold border-border/50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="rounded-2xl h-12 font-bold bg-destructive hover:bg-destructive/90 text-destructive-foreground shadow-lg shadow-destructive/20"
              onClick={() => {
                if (keyToDelete) {
                  handleRevokeKey(keyToDelete);
                  setKeyToDelete(null);
                }
              }}
            >
              Confirm Revocation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default Dashboard;
