import { useAuth0 } from "@auth0/auth0-react";
import { useEffect, useState, useRef } from "react";
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
  FileCode,
  Square,
  Activity,
  Github,
  Lock
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
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
import RepoScannerWidget from "@/components/dashboard/RepoScannerWidget";
import ThemeToggle from "@/components/ThemeToggle";
import { InlineApiKeyPanel } from "@/components/dashboard/InlineApiKeyPanel";
// import QueueMonitorWidget from "@/components/dashboard/QueueMonitorWidget";

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
  // Derive the API base URL.
  // Priority: explicit VITE_API_BASE_URL → origin extracted from the first backend's
  // baseUrl (works in production where baseUrl is an absolute URL like
  // "https://api-sandbox.01security.com/api/v1/01sbx") → "" (local dev, same origin).
  const API_BASE_URL = (() => {
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
          // Only use cross-origin API servers; same-origin relative paths return ""
          if (parsed.origin !== window.location.origin) return parsed.origin;
        }
      }
    } catch { /* local dev uses relative paths — fall through */ }
    return "";
  })();
  const { user, getAccessTokenSilently, isAuthenticated, isLoading: authLoading } = useAuth0();
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [authToken, setAuthToken] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("apps");

  // --- DEVELOPER TESTING MODE STATES & FUNCTIONS ---
  const enableDevModeEnv = (window as any)._env_?.VITE_ENABLE_DEV_MODE
    ? (window as any)._env_?.VITE_ENABLE_DEV_MODE
    : import.meta.env.VITE_ENABLE_DEV_MODE;
  const enableDevMode = enableDevModeEnv !== "false";

  const [devMode, setDevMode] = useState(false);
  const [bulkQueue, setBulkQueue] = useState<{ name: string; content: string; lang: string }[]>([]);
  const [isBulkScanning, setIsBulkScanning] = useState(false);
  const [bulkScanLogs, setBulkScanLogs] = useState<{
    name: string;
    status: 'idle' | 'scanning' | 'clean' | 'risks' | '429' | '401' | 'error';
    findingsCount?: number;
    duration?: number;
    errorMsg?: string;
    report?: any;
  }[]>([]);
  const [rateLimitCountdown, setRateLimitCountdown] = useState<number | null>(null);
  const bulkScanCancelledRef = useRef(false);

  const stopBulkSecurityAudit = () => {
    bulkScanCancelledRef.current = true;
    setIsBulkScanning(false);
    toast.info("Stopping bulk security scan...");
  };

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
          let blockLang = blocks[j].toLowerCase();
          const blockContent = blocks[j + 1]?.trim();

          // Normalize block language names
          if (blockLang === 'python') blockLang = 'py';
          else if (blockLang === 'golang') blockLang = 'go';
          else if (blockLang === 'javascript' || blockLang === 'typescript') blockLang = 'js';
          else if (blockLang === 'bash' || blockLang === 'shell') blockLang = 'sh';
          else if (blockLang === 'kubernetes') blockLang = 'k8s';

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
    bulkScanCancelledRef.current = false;

    let rateLimitUntil = 0;
    const scanPromises: Promise<void>[] = [];

    const checkRateLimitWait = async () => {
      while (Date.now() < rateLimitUntil && !bulkScanCancelledRef.current) {
        const remaining = Math.ceil((rateLimitUntil - Date.now()) / 1000);
        setRateLimitCountdown(remaining);
        await new Promise(r => setTimeout(r, 1000));
      }
      setRateLimitCountdown(null);
    };

    // Process concurrent queue
    for (let i = 0; i < bulkQueue.length; i++) {
      // Skip files that have already finished successfully
      const currentLog = bulkScanLogs[i];
      if (currentLog && (currentLog.status === 'clean' || currentLog.status === 'risks')) {
        continue;
      }

      if (bulkScanCancelledRef.current) {
        break;
      }

      await checkRateLimitWait();

      if (bulkScanCancelledRef.current) {
        break;
      }

      const item = bulkQueue[i];

      const scanTask = (async () => {
        // Update status to scanning
        setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: 'scanning' } : log));

        let attemptScan = true;
        while (attemptScan && !bulkScanCancelledRef.current) {
          await checkRateLimitWait();

          if (bulkScanCancelledRef.current) {
            break;
          }

          try {
            const apiExt = item.lang === 'k8s' ? 'yaml' : item.lang;
            const filename = item.name.includes('.') ? item.name : `${item.name}.${apiExt}`;

            // 1. Submit the job asynchronously
            const response = await fetch(`${baseUrl}/scan-jobs?async=true`, {
              method: "POST",
              headers: {
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": `Bearer ${foundKey}`
              },
              body: JSON.stringify({
                files: { [filename]: item.content },
                metadata: { job_id: `bulk_${Date.now()}_${i}` }
              })
            });

            if (bulkScanCancelledRef.current) {
              break;
            }

            const data = await response.json();

            if (response.status === 429) {
              const retryAfter = data.detail?.retry_after || data.retry_after || 60;
              const newLimit = Date.now() + retryAfter * 1000;
              if (newLimit > rateLimitUntil) {
                rateLimitUntil = newLimit;
                toast.warning(`Rate limit hit. Waiting ${retryAfter}s before retrying...`);
              }
              setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: '429', errorMsg: `Rate limit hit. Retrying in ${retryAfter}s...` } : log));
              continue;
            }

            if (!response.ok) {
              const errStatus = response.status === 401 ? '401' : 'error';
              const errMsg = data.detail || data.error || "Ingestion error";
              setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: errStatus, errorMsg: errMsg } : log));
              attemptScan = false;
              break;
            }

            // 2. Job submitted successfully, start polling loop
            const jobId = data.job_id;
            let reportData = null;

            while (true) {
              if (bulkScanCancelledRef.current) {
                break;
              }
              await new Promise(r => setTimeout(r, 5000)); // Poll every 5 seconds
              if (bulkScanCancelledRef.current) {
                break;
              }
              const pollRes = await fetch(`${baseUrl}/scan-jobs/${jobId}/report`, {
                headers: {
                  "accept": "application/json",
                  "Authorization": `Bearer ${foundKey}`
                }
              });

              if (pollRes.status === 200) {
                reportData = await pollRes.json();
                break;
              } else if (pollRes.status !== 404) {
                throw new Error("Polling failed with status " + pollRes.status);
              }
            }

            if (bulkScanCancelledRef.current) {
              setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: 'idle' } : log));
              attemptScan = false;
              break;
            }

            // 3. Process the retrieved report
            const report = reportData.report || reportData;
            const findings = Array.isArray(report)
              ? report
              : (report.findings || report.findings_list || []);

            // Exclude INFO severity from high-priority vulnerability counts so best practices don't block green status
            const vulnerabilities = findings.filter((f: any) => f.severity && f.severity.toLowerCase() !== 'info');
            const totalFindings = vulnerabilities.length;
            const finalStatus = totalFindings > 0 ? 'risks' : 'clean';

            setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? {
              ...log,
              status: finalStatus,
              findingsCount: totalFindings,
              duration: 0.8,
              report: report
            } : log));

            attemptScan = false;
          } catch (error: any) {
            console.error("Bulk scan error:", error);
            setBulkScanLogs(prev => prev.map((log, idx) => idx === i ? { ...log, status: 'error', errorMsg: error.message } : log));
            attemptScan = false;
          }
        }
      })();

      scanPromises.push(scanTask);

      // Wait 2 seconds before launching the next scan
      if (i < bulkQueue.length - 1 && !bulkScanCancelledRef.current) {
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }

    await Promise.all(scanPromises);
    setIsBulkScanning(false);
    if (bulkScanCancelledRef.current) {
      toast.warning("Bulk security audit stopped by user!");
    } else {
      toast.success("Bulk security audit finished!");
    }
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
  const [selectedBackend, setSelectedBackend] = useState<any | null>(null);
  const [subscribingApp, setSubscribingApp] = useState<any | null>(null);
  const [isSubscribing, setIsSubscribing] = useState(false);

  const fetchBackends = async () => {
    try {
      let headers: any = {};
      if (isAuthenticated) {
        try {
          const token = await getAccessTokenSilently();
          headers["Authorization"] = `Bearer ${token}`;
        } catch (err) {
          console.error("Error obtaining token for backends fetch:", err);
        }
      }
      const response = await fetch(`${API_BASE_URL}/v1/backends`, { headers });
      if (response.ok) {
        const data = await response.json();
        setBackends(data);
        // Sync selectedBackend if it's currently open
        if (selectedBackend) {
          const updated = data.find((b: any) => b.id === selectedBackend.id);
          if (updated) {
            setSelectedBackend(updated);
          }
        }
        return;
      }
    } catch (e) {
      console.error("Failed to load backends config from API, falling back to environment:", e);
    }

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
            documentationUrl: b.documentationUrl || `${defaultBase}/docs`,
            isSubscribed: b.id === "Z1_SANDBOX"
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

  // fetchKeys function
  const fetchKeys = async () => {
    try {
      setIsLoading(true);
      let token = "";
      try {
        token = await getAccessTokenSilently();
        setAuthToken(token);
      } catch (err) {
        console.warn("Auth0 not authenticated, using local mock keys:", err);
      }

      if (!token) {
        const stored = localStorage.getItem("local_mock_api_keys");
        if (stored) {
          setKeys(JSON.parse(stored));
        } else {
          setKeys([]);
        }
        return;
      }

      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        if (data.keys) {
          setKeys(data.keys.filter((k: APIKey) => !k.is_revoked));
        }
      }
    } catch (error) {
      console.error("Error fetching keys:", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchBackends();
    fetchKeys();
  }, [isAuthenticated]);

  useEffect(() => {
    const handleKeysChanged = () => {
      fetchKeys();
    };
    window.addEventListener('api-keys-changed', handleKeysChanged);
    return () => {
      window.removeEventListener('api-keys-changed', handleKeysChanged);
    };
  }, [isAuthenticated]);

  useEffect(() => {
    if (selectedBackend) {
      document.body.classList.add("hide-navbar");
    } else {
      document.body.classList.remove("hide-navbar");
    }
    return () => {
      document.body.classList.remove("hide-navbar");
    };
  }, [selectedBackend]);

  useEffect(() => {
    if (backends.length > 0 && !scannerConfig.baseUrl) {
      const defaultApp = backends.find(b => b.baseUrl) || backends[0];
      if (defaultApp && defaultApp.baseUrl) {
        const backend = defaultApp.id;
        const baseUrl = defaultApp.baseUrl;
        const backendKeys = keys.filter(k => k.backend === backend);
        let foundKey = "";
        for (const k of backendKeys) {
          const saved = localStorage.getItem(`bound_key_${k.id}`);
          if (saved) {
            foundKey = saved;
            break;
          }
        }
        const keyToUse = foundKey || authToken;
        setScannerConfig({
          isOpen: false,
          backend,
          baseUrl,
          apiKey: keyToUse
        });
      }
    }
  }, [backends, keys, authToken, scannerConfig.baseUrl]);



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

    const keyToUse = foundKey || authToken;

    if (!keyToUse) {
      toast.error(`No API Key or session token found for ${backend}. Please create one or login.`);
      return;
    }

    setScannerConfig({
      isOpen: false,
      backend,
      baseUrl,
      apiKey: keyToUse
    });
    setActiveTab("quick-scan");
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

    const keyToUse = foundKey || authToken;

    if (!keyToUse) {
      toast.error(`No API Key or session token found for ${backend}. Please create one in the API Management tab.`);
      return;
    }
    // Bind both execution token and management token for Swagger
    const token = await getAccessTokenSilently();
    document.cookie = `inspector_auth=${token}; SameSite=Lax; Path=/; Max-Age=${60 * 60 * 24}`;
    document.cookie = `execution_token=${keyToUse}; SameSite=Lax; Path=/; Max-Age=${60 * 60 * 24 * 7}`;

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

  const handleSubscribe = async () => {
    if (!subscribingApp) return;
    try {
      setIsSubscribing(true);
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        console.warn("Auth0 not authenticated, using local mock subscription:", err);
      }

      if (!token) {
        // Mock subscription for local dev without auth
        setBackends(prev =>
          prev.map(b => (b.id === subscribingApp.id ? { ...b, isSubscribed: true } : b))
        );
        toast.success(`Successfully subscribed to ${subscribingApp.name}!`);
        setSubscribingApp(null);
        return;
      }

      const response = await fetch(`${API_BASE_URL}/v1/subscriptions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ backend_id: subscribingApp.id })
      });
      if (response.ok) {
        toast.success(`Successfully subscribed to ${subscribingApp.name}!`);
        await fetchBackends();
        setSubscribingApp(null);
      } else {
        const err = await response.json();
        throw new Error(err.detail || "Failed to subscribe");
      }
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setIsSubscribing(false);
    }
  };

  return (
    <div className={cn(
      "min-h-screen px-6 sm:px-8 mx-auto transition-all duration-300",
      selectedBackend ? "max-w-none w-full pt-10 pb-6" : "max-w-7xl pt-32 pb-20"
    )}>
      {!selectedBackend && (
        <header className="mb-12">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <LayoutDashboard className="w-6 h-6" />
            </div>
            <h1 className="text-4xl font-display font-black tracking-tight">01 Sandbox Dashboard</h1>
          </div>
          <p className="text-muted-foreground text-lg max-w-2xl">
            Securely manage your API integrations, track sandbox activity, and scale your intelligence infrastructure.
          </p>
        </header>
      )}

      {selectedBackend ? (
        // Consolidated Workspace Drawer/Console per Backend
        <div className="space-y-5 animate-in fade-in duration-300">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-border/40 pb-4 gap-4">
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedBackend(null)}
                className="h-9 rounded-xl border border-border/50 hover:bg-secondary/30 text-muted-foreground hover:text-foreground font-bold flex items-center gap-1.5"
              >
                <ChevronRight className="w-4 h-4 rotate-180" />
                Back
              </Button>
              <Separator orientation="vertical" className="h-6" />
              <div className="flex items-center gap-3">
                <div className={cn(
                  "p-2 rounded-xl border bg-primary/10 text-primary border-primary/20"
                )}>
                  {selectedBackend.icon === "terminal" ? <Terminal className="w-5 h-5" /> : <Box className="w-5 h-5" />}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-3xl font-display font-black tracking-tight">{selectedBackend.name}</h2>
                    <Badge variant="outline" className="bg-emerald-500/10 text-emerald-500 border-emerald-500/20 text-[9px] font-black uppercase py-0">Subscribed</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">{selectedBackend.description}</p>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <ThemeToggle />
              <InlineApiKeyPanel backendId={selectedBackend.id} />
              <Button
                onClick={() => bindAndVisit(selectedBackend.id, selectedBackend.documentationUrl)}
                variant="outline"
                className="rounded-xl font-bold h-9 text-xs flex items-center gap-1.5 border-border/50 hover:bg-secondary/20"
              >
                View Docs
                <ExternalLinkIcon className="w-3.5 h-3.5 opacity-55" />
              </Button>
            </div>
          </div>

          <Tabs defaultValue="quick" className="space-y-4">
            <TabsList className="bg-secondary/30 p-1.5 rounded-2xl border border-border/50 h-auto gap-1 flex-nowrap overflow-x-auto no-scrollbar justify-start">
              <TabsTrigger value="quick" className="rounded-xl px-5 py-2.5 text-xs font-bold data-[state=active]:bg-background">Quick Scanner</TabsTrigger>
              <TabsTrigger value="repo" className="rounded-xl px-5 py-2.5 text-xs font-bold data-[state=active]:bg-background">Repository Scanner</TabsTrigger>
            </TabsList>


            <TabsContent value="quick" className="animate-in fade-in-50 duration-300">
              <SecurityScanner
                isOpen={false}
                onClose={() => { }}
                backend={selectedBackend.id}
                baseUrl={selectedBackend.baseUrl}
                apiKey={
                  (() => {
                    const backendKeys = keys.filter(k => k.backend === selectedBackend.id);
                    let foundKey = "";
                    for (const k of backendKeys) {
                      const saved = localStorage.getItem(`bound_key_${k.id}`);
                      if (saved) return saved;
                    }
                    return authToken;
                  })()
                }
                inline={true}
                onSwitchTab={setActiveTab}
              />
            </TabsContent>

            <TabsContent value="repo" className="animate-in fade-in-50 duration-300">
              <RepoScannerWidget
                apiBaseUrl={API_BASE_URL}
                keys={keys}
                authToken={authToken}
                inline={true}
                onSwitchTab={setActiveTab}
                backendId={selectedBackend.id}
              />
            </TabsContent>
          </Tabs>
        </div>
      ) : (
        // Standard Applications View (with locks)
        <div className="space-y-8 animate-in fade-in-50 duration-500">
          {enableDevMode && (
            <div className="mb-8 flex items-center justify-between p-6 rounded-[2rem] bg-secondary/15 border border-border/40 backdrop-blur-sm shadow-xl shadow-primary/5 transition-all">
              <div>
                <h3 className="text-lg font-black tracking-tight flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse shadow-[0_0_8px_rgba(99,102,241,0.6)]" />
                  Developer Ingestion & Testing Mode
                </h3>
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
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-start">
            {backends.map((app) => {
              const IconComponent = app.icon === "terminal" ? Terminal : (app.icon === "box" ? Box : Code);
              const colorClass = app.color === "indigo" ? "bg-indigo-500/10 text-indigo-500 border-indigo-500/20" : "bg-emerald-500/10 text-emerald-500 border-emerald-500/20";

              return (
                <Card key={app.id} className={cn(
                  "group relative overflow-hidden rounded-[2rem] border-border/50 bg-background/50 backdrop-blur-sm transition-all hover:border-primary/50 hover:shadow-2xl hover:shadow-primary/5",
                  !app.isSubscribed ? "opacity-90 border-zinc-800/80" : ""
                )}>
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
                    {app.isSubscribed ? (
                      <>
                        <Button
                          className="w-full bg-primary hover:bg-primary/90 text-primary-foreground rounded-2xl h-12 font-bold flex items-center justify-center gap-2 transition-all"
                          onClick={() => setSelectedBackend(app)}
                        >
                          <Box className="w-4 h-4" />
                          Open Console
                        </Button>
                        <Button
                          className="w-full bg-white/5 hover:bg-white/10 text-foreground border border-border/50 rounded-2xl h-12 font-bold flex items-center justify-center gap-2 transition-all"
                          onClick={() => bindAndVisit(app.id, app.documentationUrl)}
                        >
                          View Documentation
                          <ExternalLinkIcon className="w-4 h-4 opacity-50" />
                        </Button>
                      </>
                    ) : (
                      <Button
                        className="w-full bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 rounded-2xl h-12 font-bold flex items-center justify-center gap-2 transition-all animate-pulse"
                        onClick={() => setSubscribingApp(app)}
                      >
                        <Lock className="w-4 h-4" />
                        Subscribe & Unlock
                      </Button>
                    )}

                    {app.isSubscribed && devMode && app.baseUrl && (
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

                            <div className="flex gap-2 w-full animate-in fade-in duration-200">
                              <Button
                                onClick={() => runBulkSecurityAudit(app.id, app.baseUrl)}
                                disabled={isBulkScanning}
                                className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-2xl h-11 font-bold flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/10 transition-all disabled:opacity-90 disabled:cursor-not-allowed"
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

                              {isBulkScanning && (
                                <Button
                                  type="button"
                                  onClick={stopBulkSecurityAudit}
                                  className="bg-rose-600 hover:bg-rose-500 text-white rounded-2xl h-11 px-4 font-bold flex items-center justify-center gap-2 shadow-lg transition-all animate-in zoom-in duration-200"
                                >
                                  <Square className="w-4 h-4 fill-current" />
                                  Stop
                                </Button>
                              )}
                            </div>

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
                                  <div
                                    key={idx}
                                    className="flex items-center justify-between py-1.5 border-b border-white/5 last:border-0 transition-all"
                                  >
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
        </div>
      )}

      {/* Subscription Dialog Modal */}
      <Dialog open={!!subscribingApp} onOpenChange={(open) => !open && setSubscribingApp(null)}>
        <DialogContent className="rounded-3xl border-border/50 bg-background/95 backdrop-blur-md max-w-sm">
          <DialogHeader className="items-center text-center">
            <div className="p-4 rounded-full bg-primary/10 border border-primary/20 text-primary mb-4">
              <ShieldCheck className="w-10 h-10 animate-pulse" />
            </div>
            <DialogTitle className="font-black text-2xl tracking-tight">Unlock {subscribingApp?.name}</DialogTitle>
            <DialogDescription className="text-muted-foreground text-xs max-w-xs mt-2">
              Subscribe now to deploy production security pipelines and sandboxes for code validation.
            </DialogDescription>
          </DialogHeader>

          <div className="bg-zinc-950/40 border border-white/5 rounded-2xl p-4 my-4 space-y-3 font-medium text-xs">
            <div className="flex justify-between items-center text-muted-foreground">
              <span>Subscription Tier:</span>
              <span className="text-foreground font-bold">Sandbox Pro</span>
            </div>
            <div className="flex justify-between items-center text-muted-foreground">
              <span>Billing Cycle:</span>
              <span className="text-foreground font-bold">Monthly Recurring</span>
            </div>
            <Separator className="border-white/5" />
            <div className="flex justify-between items-center text-sm font-bold">
              <span>Total Price:</span>
              <span className="text-primary font-black">$49.00 / mo</span>
            </div>
          </div>

          <DialogFooter className="flex flex-col gap-2 sm:flex-col">
            <Button
              className="w-full bg-primary hover:bg-primary/95 text-primary-foreground font-bold h-11 rounded-xl"
              onClick={handleSubscribe}
              disabled={isSubscribing}
            >
              {isSubscribing ? "Processing Transaction..." : "Confirm & Subscribe"}
            </Button>
            <Button
              variant="ghost"
              className="w-full h-11 rounded-xl font-semibold text-muted-foreground hover:bg-secondary/20"
              onClick={() => setSubscribingApp(null)}
            >
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

// Sub-component for managing keys per backend
interface BackendKeysManagementProps {
  backendId: string;
  API_BASE_URL: string;
  keys: any[];
  onRefresh: () => void;
  getAccessTokenSilently: any;
}

const BackendKeysManagement = ({
  backendId,
  API_BASE_URL,
  keys,
  onRefresh,
  getAccessTokenSilently
}: BackendKeysManagementProps) => {
  const [name, setName] = useState("");
  const [ttl, setTtl] = useState("days");
  const [ttlValue, setTtlValue] = useState("30");
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<any | null>(null);
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);

  const filteredKeys = keys.filter(k => k.backend === backendId);

  const handleCreateKey = async () => {
    const sanitizedName = name.trim();
    if (!sanitizedName) {
      toast.error("Please enter a valid key name");
      return;
    }

    try {
      setIsCreating(true);
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        console.warn("Auth0 not authenticated, using local mock key creation:", err);
      }

      let ttl_seconds: number | null = null;
      if (ttl !== "never") {
        const val = parseInt(ttlValue);
        if (isNaN(val) || val <= 0) {
          toast.error("TTL must be a positive integer");
          return;
        }

        const multipliers: Record<string, number> = {
          minutes: 60,
          hours: 3600,
          days: 86400,
          months: 2592000,
          years: 31536000,
        };
        ttl_seconds = val * multipliers[ttl];
      }

      if (!token) {
        // Local mock API key generation
        const mockKeyId = "key_" + Math.random().toString(36).substr(2, 9);
        const mockKeyValue = "z1_" + Math.random().toString(36).substr(2, 24);
        const mockKeyObj = {
          id: mockKeyId,
          name: sanitizedName,
          backend: backendId,
          prefix: "z1_mock",
          created_at: new Date().toISOString(),
          expires_at: ttl === "never" ? "Never" : new Date(Date.now() + (ttl_seconds || 0) * 1000).toISOString(),
          is_revoked: false,
        };

        const stored = localStorage.getItem("local_mock_api_keys");
        const currentList = stored ? JSON.parse(stored) : [];
        currentList.push(mockKeyObj);
        localStorage.setItem("local_mock_api_keys", JSON.stringify(currentList));
        localStorage.setItem(`bound_key_${mockKeyId}`, mockKeyValue);

        setNewKey({
          api_key_id: mockKeyId,
          api_key: mockKeyValue,
          name: sanitizedName,
          expires_at: mockKeyObj.expires_at,
        });

        onRefresh();
        setName("");
        toast.success("API Key generated successfully (Local Dev)!");
        window.dispatchEvent(new Event("api-keys-changed"));
        return;
      }

      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          name: sanitizedName,
          backend: backendId,
          ttl_seconds,
          user_email: user?.email || undefined,
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to generate key");
      }

      const data = await response.json();
      setNewKey(data);
      localStorage.setItem(`bound_key_${data.api_key_id}`, data.api_key);

      onRefresh();
      setName("");
      toast.success("API Key generated successfully!");
      window.dispatchEvent(new Event("api-keys-changed"));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevokeKey = async (id: string) => {
    try {
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        // Fallback
      }

      if (!token) {
        const stored = localStorage.getItem("local_mock_api_keys");
        if (stored) {
          const currentList = JSON.parse(stored);
          const updatedList = currentList.filter((k: any) => k.id !== id);
          localStorage.setItem("local_mock_api_keys", JSON.stringify(updatedList));
        }
        toast.info("Mock key revoked successfully (Local Dev)");
        onRefresh();
        window.dispatchEvent(new Event("api-keys-changed"));
        return;
      }

      await fetch(`${API_BASE_URL}/v1/api-keys/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.info("Key revoked successfully");
      onRefresh();
      window.dispatchEvent(new Event("api-keys-changed"));
    } catch (error) {
      toast.error("Failed to revoke key");
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
      {/* Create Key Card */}
      <Card className="bg-secondary/5 border-border/30 p-6 rounded-2xl flex flex-col justify-between h-fit">
        <div className="space-y-4">
          <h3 className="font-bold text-base flex items-center gap-2">
            <Key className="w-4 h-4 text-primary" />
            Generate Service Key
          </h3>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Create API keys to integrate and authenticate your CLI tool or CI/CD pipelines directly with this backend.
          </p>

          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Key Name</label>
              <Input
                placeholder="e.g. Jenkins Scan Pipeline"
                value={name}
                onChange={e => setName(e.target.value)}
                className="bg-background/50 border-border/50 rounded-xl"
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Expiration</label>
                <Select value={ttl} onValueChange={setTtl}>
                  <SelectTrigger className="bg-background/50 border-border/50 rounded-xl">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="minutes">Minutes</SelectItem>
                    <SelectItem value="hours">Hours</SelectItem>
                    <SelectItem value="days">Days</SelectItem>
                    <SelectItem value="never">Never</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {ttl !== "never" && (
                <div className="space-y-1">
                  <label className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Value</label>
                  <Input
                    type="number"
                    value={ttlValue}
                    onChange={e => setTtlValue(e.target.value)}
                    className="bg-background/50 border-border/50 rounded-xl"
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        <Button
          onClick={handleCreateKey}
          disabled={isCreating}
          className="w-full mt-6 bg-primary hover:bg-primary/95 text-primary-foreground font-bold h-10 rounded-xl"
        >
          {isCreating ? "Generating Key..." : "Generate Key"}
        </Button>
      </Card>

      {/* Active Keys List Card */}
      <Card className="lg:col-span-2 bg-secondary/5 border-border/30 p-6 rounded-2xl flex flex-col min-h-[300px]">
        <div className="flex items-center justify-between pb-4 border-b border-border/30 mb-4">
          <h3 className="font-bold text-base flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-primary" />
            Active Service Keys
          </h3>
          <Badge variant="outline" className="bg-primary/5 text-primary border-primary/20">
            {filteredKeys.length} Keys
          </Badge>
        </div>

        {newKey && (
          <div className="mb-6 p-4 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-xs text-foreground animate-in zoom-in-95 duration-200">
            <div className="font-black text-indigo-400 uppercase tracking-widest text-[9px] mb-1">
              Secret Key Generated - Copy it now!
            </div>
            <p className="text-muted-foreground mb-3">For security, you won't be able to see this secret key again.</p>
            <div className="flex items-center gap-2 bg-zinc-950 p-2.5 rounded-lg border border-white/5 font-mono text-[11px] select-all">
              <span className="truncate flex-1 text-zinc-200">{newKey.api_key}</span>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 w-8 p-0 rounded-lg"
                onClick={() => {
                  navigator.clipboard.writeText(newKey.api_key);
                  toast.success("Secret key copied!");
                }}
              >
                <Copy className="w-3.5 h-3.5" />
              </Button>
            </div>
          </div>
        )}

        <ScrollArea className="flex-1 max-h-[350px]">
          {filteredKeys.length === 0 ? (
            <div className="h-[200px] flex flex-col items-center justify-center text-center opacity-35">
              <Key className="w-10 h-10 text-muted-foreground mb-2" />
              <p className="text-xs font-bold uppercase tracking-wider">No active service keys</p>
              <p className="text-[10px] text-muted-foreground mt-1">Generate a key to get started</p>
            </div>
          ) : (
            <div className="space-y-3 pr-2">
              {filteredKeys.map((key) => (
                <div key={key.id} className="p-4 rounded-xl border border-border/40 bg-zinc-950/20 flex items-center justify-between hover:bg-zinc-950/40 transition-all">
                  <div className="space-y-1 max-w-[70%]">
                    <h4 className="font-bold text-xs truncate">{key.name}</h4>
                    <div className="flex items-center gap-3 text-[10px] text-muted-foreground font-mono">
                      <span>Prefix: {key.prefix}...</span>
                      <span>Expires: {key.expires_at ? new Date(key.expires_at).toLocaleDateString() : "Never"}</span>
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 text-xs font-bold text-destructive hover:bg-destructive/10 rounded-lg"
                    onClick={() => setKeyToDelete(key.id)}
                  >
                    Revoke
                  </Button>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>

        {/* Delete Confirmation Alert */}
        <AlertDialog open={!!keyToDelete} onOpenChange={(open) => !open && setKeyToDelete(null)}>
          <AlertDialogContent className="rounded-3xl border-border/50 bg-background/95 backdrop-blur-md">
            <AlertDialogHeader>
              <AlertDialogTitle className="font-black text-xl tracking-tight">Revoke Service Key?</AlertDialogTitle>
              <AlertDialogDescription className="text-muted-foreground">
                This action is permanent and cannot be undone. Any integrations or scripts using this key will immediately fail to authenticate.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="gap-2">
              <AlertDialogCancel className="rounded-xl font-bold h-11 border-border/50">Cancel</AlertDialogCancel>
              <AlertDialogAction
                className="rounded-xl font-bold h-11 bg-rose-600 hover:bg-rose-500 text-white"
                onClick={() => {
                  if (keyToDelete) {
                    handleRevokeKey(keyToDelete);
                    setKeyToDelete(null);
                  }
                }}
              >
                Revoke Key
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </Card>
    </div>
  );
};

export default Dashboard;
