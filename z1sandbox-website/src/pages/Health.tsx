import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Database,
  Zap,
  Cpu,
  RefreshCw,
  CheckCircle,
  AlertTriangle,
  Server,
  Clock,
  ArrowRight,
  Copy,
  Check
} from "lucide-react";

interface DependencyStatus {
  status: string;
  details: string;
}

interface HealthData {
  status: string;
  backend: string;
  healthy: boolean;
  dependencies: {
    database: DependencyStatus;
    cache: DependencyStatus;
    queue: DependencyStatus;
    opensandbox: DependencyStatus;
  };
}

const Health = () => {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [copied, setCopied] = useState(false);

  // Dynamically resolve base API URL with fallbacks
  const API_BASE_URL = (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "";
  let resolvedHealthUrl = "https://api-sandbox.01security.com/health";
  if (API_BASE_URL) {
    const cleanBase = API_BASE_URL.replace(/\/api\/z1sandbox\/?$/, "").replace(/\/v1\/?$/, "");
    resolvedHealthUrl = `${cleanBase}/health`;
  }

  const fetchHealthStatus = async () => {
    setIsRefreshing(true);
    try {
      const response = await fetch(resolvedHealthUrl, {
        method: "GET",
        headers: {
          "Accept": "application/json",
        }
      });

      if (!response.ok && response.status !== 500) {
        throw new Error(`Server returned status ${response.status}`);
      }

      const healthJson = await response.json();
      setData(healthJson);
      setError(null);
    } catch (err: any) {
      console.error("Health Check Fetch Failure:", err);
      setError(err.message || "Failed to reach the cluster API server");
    } finally {
      setLoading(false);
      setIsRefreshing(false);
      setLastUpdated(new Date());
    }
  };

  useEffect(() => {
    fetchHealthStatus();
    // Auto-refresh health status every 30 seconds
    const interval = setInterval(fetchHealthStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleCopyUrl = () => {
    navigator.clipboard.writeText(resolvedHealthUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Helper to map dependency keys to descriptive names and icons ...
  const getDependencyMeta = (key: string) => {
    switch (key) {
      case "database":
        return {
          title: "PostgreSQL Database",
          icon: <Database className="w-5 h-5 text-indigo-500 group-hover:scale-110 transition-transform duration-300" />,
          description: "Relational storage for API scanning history, cryptographic keys, and metadata."
        };
      case "cache":
        return {
          title: "Redis Cache",
          icon: <Zap className="w-5 h-5 text-amber-500 group-hover:scale-110 transition-transform duration-300" />,
          description: "Distributed caching layer facilitating lightning-fast session validation."
        };
      case "queue":
        return {
          title: "Background Task Queue",
          icon: <Server className="w-5 h-5 text-teal-500 group-hover:scale-110 transition-transform duration-300" />,
          description: "Message broker coordinating asynchronous security scanning pipelines."
        };
      case "opensandbox":
        return {
          title: "OpenSandbox Core Engine",
          icon: <Cpu className="w-5 h-5 text-rose-500 group-hover:scale-110 transition-transform duration-300" />,
          description: "Isolated gRPC hypervisor server running secure runtime container sandboxes."
        };
      default:
        return {
          title: key,
          icon: <Cpu className="w-5 h-5" />,
          description: ""
        };
    };
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-background/95 pt-32 pb-12 px-4 sm:px-6 lg:px-8 relative overflow-hidden">

      {/* Decorative Blur Spheres */}
      <div className="absolute top-1/4 left-1/10 w-96 h-96 rounded-full bg-primary/5 blur-[120px] pointer-events-none -z-10" />
      <div className="absolute bottom-1/4 right-1/10 w-96 h-96 rounded-full bg-indigo-500/5 blur-[120px] pointer-events-none -z-10" />

      <div className="max-w-4xl mx-auto space-y-8 relative z-10">

        {/* Breadcrumb / Title area */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between space-y-4 md:space-y-0 border-b border-border/40 pb-6">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              System Monitor <ArrowRight className="w-3 h-3" /> Status
            </span>
            <h1 className="text-3xl font-extrabold tracking-tight mt-1 text-foreground">
              Cluster Infrastructure Health
            </h1>
            <p className="text-sm text-muted-foreground mt-1.5">
              Real-time connectivity tracker and dependency verification.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="w-3.5 h-3.5" />
              Last updated: {lastUpdated.toLocaleTimeString()}
            </span>
            <button
              onClick={fetchHealthStatus}
              disabled={loading || isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 bg-secondary text-secondary-foreground border border-border/80 rounded-lg text-xs font-medium hover:bg-secondary/80 active:scale-95 transition-all disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        <AnimatePresence mode="wait">
          {loading ? (
            <motion.div
              key="loading"
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -15 }}
              className="flex flex-col items-center justify-center py-20 space-y-4"
            >
              <div className="relative w-12 h-12 flex items-center justify-center">
                <span className="absolute inline-flex h-full w-full rounded-full bg-primary/20 animate-ping" />
                <RefreshCw className="w-8 h-8 text-primary animate-spin" />
              </div>
              <p className="text-sm font-medium text-muted-foreground animate-pulse">
                Querying active Kubernetes service pings...
              </p>
            </motion.div>
          ) : error ? (
            <motion.div
              key="error"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-destructive/5 border border-destructive/20 rounded-xl p-8 space-y-6 text-center shadow-lg"
            >
              <div className="inline-flex p-3 bg-destructive/10 text-destructive rounded-full">
                <AlertTriangle className="w-8 h-8 animate-bounce" />
              </div>
              <div className="space-y-2 max-w-lg mx-auto">
                <h3 className="text-lg font-bold text-foreground">API Server Unreachable</h3>
                <p className="text-sm text-muted-foreground">
                  The frontend was unable to establish a secure handshake with the cluster's health suite. This is typically due to strict local firewalls or temporary gateway routing downtime.
                </p>
                <div className="bg-destructive/10 text-destructive-foreground px-4 py-2.5 rounded-lg text-xs font-mono break-all inline-block mt-2">
                  {error}
                </div>
              </div>

              <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
                <button
                  onClick={fetchHealthStatus}
                  className="px-4 py-2 bg-primary text-primary-foreground font-semibold rounded-lg text-sm hover:bg-primary/95 active:scale-95 transition-all shadow-md"
                >
                  Retry Connection
                </button>
                <button
                  onClick={handleCopyUrl}
                  className="flex items-center gap-1.5 px-4 py-2 bg-secondary text-secondary-foreground border border-border rounded-lg text-sm hover:bg-secondary/90 transition-all"
                >
                  {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                  {copied ? "Copied!" : "Copy Backend URL"}
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="content"
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              className="space-y-8"
            >
              {/* Overall Status Banner */}
              <div className={`border rounded-xl p-6 relative overflow-hidden transition-all shadow-md ${data?.healthy
                ? "bg-emerald-500/5 border-emerald-500/20"
                : "bg-amber-500/5 border-amber-500/20"
                }`}>
                <div className="flex items-start gap-4">
                  <div className={`p-2.5 rounded-xl ${data?.healthy ? "bg-emerald-500/10 text-emerald-500" : "bg-amber-500/10 text-amber-500"
                    }`}>
                    {data?.healthy ? (
                      <CheckCircle className="w-8 h-8 animate-pulse" />
                    ) : (
                      <AlertTriangle className="w-8 h-8 animate-pulse" />
                    )}
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <h2 className="text-xl font-bold">
                        {data?.healthy ? "All Systems Operational" : "Service Status Degraded"}
                      </h2>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xxs font-bold uppercase tracking-wider ${data?.healthy
                        ? "bg-emerald-500/20 text-emerald-500 animate-pulse"
                        : "bg-amber-500/20 text-amber-500 animate-pulse"
                        }`}>
                        {data?.status}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground max-w-xl">
                      {data?.healthy
                        ? "Every system microservice is actively pinging and executing workflows under safe load parameters."
                        : "One or more background nodes are currently timing out or reporting validation errors. Restarts may trigger automatically."}
                    </p>
                  </div>
                </div>

                {/* Sub-badge indicating cluster backend */}
                <div className="absolute top-4 right-4 hidden sm:block bg-secondary text-secondary-foreground border border-border/60 text-xxs font-mono px-2 py-0.5 rounded-md">
                  cluster: {data?.backend}
                </div>
              </div>

              {/* Grid Layout of Dependencies */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {data && Object.entries(data.dependencies).map(([key, value]) => {
                  const meta = getDependencyMeta(key);
                  const isDepHealthy = value.status === "healthy";
                  return (
                    <div
                      key={key}
                      className="group bg-card hover:bg-card/80 border border-border/60 hover:border-primary/40 rounded-xl p-5 shadow-sm hover:shadow-md transition-all duration-300 flex flex-col justify-between"
                    >
                      <div className="space-y-4">
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-3">
                            <div className="p-2 bg-secondary border border-border/80 rounded-lg group-hover:bg-secondary/70 transition-colors">
                              {meta.icon}
                            </div>
                            <h3 className="font-bold text-foreground group-hover:text-primary transition-colors text-sm sm:text-base">
                              {meta.title}
                            </h3>
                          </div>

                          <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xxs font-bold uppercase ${isDepHealthy
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                            : "bg-destructive/10 text-destructive"
                            }`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${isDepHealthy ? "bg-emerald-500 animate-ping" : "bg-destructive animate-ping"
                              }`} />
                            {value.status}
                          </span>
                        </div>

                        <p className="text-xs text-muted-foreground leading-relaxed">
                          {meta.description}
                        </p>
                      </div>

                      <div className="mt-5 pt-3 border-t border-border/40 flex flex-col gap-1.5">
                        <span className="text-xxs font-semibold uppercase text-muted-foreground">Connection Trace:</span>
                        <div className="bg-secondary/40 border border-border/30 rounded px-2.5 py-1.5 text-xxs font-mono text-foreground overflow-x-auto whitespace-nowrap scrollbar-thin">
                          {value.details}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Footer info card */}
        <div className="border border-border/40 rounded-xl p-4 bg-secondary/20 flex flex-col sm:flex-row items-center justify-between text-xs text-muted-foreground gap-3">
          <span className="flex items-center gap-1.5">
            <Server className="w-3.5 h-3.5" />
            Direct Cluster Endpoint: <code className="font-semibold text-foreground break-all">{resolvedHealthUrl}</code>
          </span>
          <span className="text-xxs uppercase tracking-wider font-mono">
            State: {data?.healthy ? "STABLE" : "DEGRADED"}
          </span>
        </div>

      </div>
    </div>
  );
};

export default Health;
