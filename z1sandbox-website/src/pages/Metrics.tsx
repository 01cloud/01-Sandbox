import { useState, useEffect } from "react";
import { Activity, Clock, RefreshCw, ExternalLink, Shield } from "lucide-react";
import { cn } from "@/lib/utils";

export default function Metrics() {
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Dynamically resolve base API URL with fallbacks
  const API_BASE_URL = import.meta.env.DEV ? "" : ((window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "");
  let cleanBase = import.meta.env.DEV ? "" : "https://api-sandbox.01security.com";
  if (API_BASE_URL) {
    cleanBase = API_BASE_URL.replace(/\/api\/z1sandbox\/?$/, "").replace(/\/v1\/?$/, "");
  }

  // The Grafana subpath is /grafana, and the dashboard UID is codeinspector-main
  const grafanaDashboardUrl = `${cleanBase}/grafana/d/codeinspector-main/codeinspector-system-dashboard?orgId=1&kiosk`;

  const handleRefresh = () => {
    setIsRefreshing(true);
    setLastUpdated(new Date());

    // Simple reload of the iframe
    const iframe = document.getElementById("grafana-iframe") as HTMLIFrameElement;
    if (iframe) {
      iframe.src = iframe.src;
    }

    setTimeout(() => {
      setIsRefreshing(false);
    }, 1000);
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-background/95 pt-32 pb-12 px-4 sm:px-6 lg:px-8 relative overflow-hidden">
      {/* Decorative Blur Spheres */}
      <div className="absolute top-1/4 left-1/10 w-96 h-96 rounded-full bg-primary/5 blur-[120px] pointer-events-none -z-10" />
      <div className="absolute bottom-1/4 right-1/10 w-96 h-96 rounded-full bg-indigo-500/5 blur-[120px] pointer-events-none -z-10" />

      <div className="max-w-7xl mx-auto space-y-8 relative z-10">

        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between space-y-4 md:space-y-0 border-b border-border/40 pb-6">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              System Monitor <Activity className="w-3.5 h-3.5" /> Performance Metrics
            </span>
            <h1 className="text-3xl font-extrabold tracking-tight mt-1 text-foreground">
              CodeInspector Live Telemetry
            </h1>
            <p className="text-sm text-muted-foreground mt-1.5">
              Real-time API response times, scan task workloads, and active sandbox queue statistics.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="w-3.5 h-3.5" />
              Last sync: {lastUpdated.toLocaleTimeString()}
            </span>

            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 bg-secondary text-secondary-foreground border border-border/80 rounded-lg text-xs font-medium hover:bg-secondary/80 active:scale-95 transition-all"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", isRefreshing && "animate-spin")} />
              Sync
            </button>

            <a
              href={grafanaDashboardUrl.replace("&kiosk", "")}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-1.5 bg-primary text-primary-foreground rounded-lg text-xs font-semibold hover:bg-primary/95 active:scale-95 transition-all shadow-md"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Open Grafana
            </a>
          </div>
        </div>

        {/* Dashboard IFrame Container */}
        <div className="relative border border-border/50 rounded-[2.5rem] bg-background/50 backdrop-blur-sm shadow-2xl p-2 overflow-hidden min-h-[680px]">
          <iframe
            id="grafana-iframe"
            src={grafanaDashboardUrl}
            className="w-full h-[660px] rounded-[2rem] border-0 bg-transparent"
            title="Grafana Dashboard"
            allow="autoplay; clipboard-write; encrypted-media; picture-in-picture"
            sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
          />
        </div>

        {/* Security Warning / Note */}
        <div className="border border-border/40 rounded-2xl p-4 bg-secondary/20 flex flex-col sm:flex-row items-center justify-between text-xs text-muted-foreground gap-3">
          <span className="flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-indigo-500" />
            Embedded Grafana panel loading directly from your secure backend cluster.
          </span>
          <span className="text-xxs uppercase tracking-wider font-mono">
            Origin: {cleanBase}
          </span>
        </div>

      </div>
    </div>
  );
}
