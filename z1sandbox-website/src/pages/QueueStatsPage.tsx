import { useState, useEffect } from "react";
import { Zap, Code, AlertTriangle, CheckCircle2, Layers, RefreshCw, Activity, Clock } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth0 } from "@auth0/auth0-react";
import { toast } from "sonner";

interface QueueStat {
  depth: number;
  consumers: number;
  throughput: number;
}

interface QueueStatsResponse {
  available: boolean;
  queues: Record<string, QueueStat>;
}

export default function QueueStatsPage() {
  const { getAccessTokenSilently, isAuthenticated } = useAuth0();
  const [queueStats, setQueueStats] = useState<QueueStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isRequeueing, setIsRequeueing] = useState(false);

  // Dynamically resolve base API URL with fallbacks
  const API_BASE_URL = (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "";
  let resolvedUrl = "https://api-sandbox.01security.com/queue-stats";
  let requeueUrl = "https://api-sandbox.01security.com/v1/queue/requeue-failed";
  if (API_BASE_URL) {
    const cleanBase = API_BASE_URL.replace(/\/api\/z1sandbox\/?$/, "").replace(/\/v1\/?$/, "");
    resolvedUrl = `${cleanBase}/queue-stats`;
    requeueUrl = `${cleanBase}/v1/queue/requeue-failed`;
  }

  const handleRequeueFailed = async () => {
    if (!isAuthenticated) {
      toast.error("You must be logged in to requeue failed jobs.");
      return;
    }
    setIsRequeueing(true);
    try {
      const token = await getAccessTokenSilently();
      const response = await fetch(requeueUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({})
      });

      const data = await response.json();
      if (response.ok) {
        toast.success(`Successfully re-queued ${data.requeued || 0} failed job(s)!`);
        fetchQueueStats();
      } else {
        toast.error(data.detail || "Failed to re-queue jobs.");
      }
    } catch (err: any) {
      console.error("Error re-queueing jobs:", err);
      toast.error(err.message || "An unexpected error occurred.");
    } finally {
      setIsRequeueing(false);
    }
  };

  const fetchQueueStats = async () => {
    setIsRefreshing(true);
    try {
      const response = await fetch(resolvedUrl);
      if (response.ok) {
        const data = await response.json();
        setQueueStats(data);
      }
    } catch (err) {
      console.error("Error fetching queue stats:", err);
    } finally {
      setLoading(false);
      setIsRefreshing(false);
      setLastUpdated(new Date());
    }
  };

  useEffect(() => {
    fetchQueueStats();
    const interval = setInterval(fetchQueueStats, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-background/95 pt-32 pb-12 px-4 sm:px-6 lg:px-8 relative overflow-hidden">
      {/* Decorative Blur Spheres */}
      <div className="absolute top-1/4 left-1/10 w-96 h-96 rounded-full bg-primary/5 blur-[120px] pointer-events-none -z-10" />
      <div className="absolute bottom-1/4 right-1/10 w-96 h-96 rounded-full bg-indigo-500/5 blur-[120px] pointer-events-none -z-10" />

      <div className="max-w-5xl mx-auto space-y-8 relative z-10">

        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between space-y-4 md:space-y-0 border-b border-border/40 pb-6">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              System Monitor <Activity className="w-3.5 h-3.5" /> Queue Telemetry
            </span>
            <h1 className="text-3xl font-extrabold tracking-tight mt-1 text-foreground">
              RabbitMQ Queue Infrastructure
            </h1>
            <p className="text-sm text-muted-foreground mt-1.5">
              Real-time message broker depth, active consumers, and rolling processing throughput.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="w-3.5 h-3.5" />
              Last updated: {lastUpdated.toLocaleTimeString()}
            </span>
            <button
              onClick={fetchQueueStats}
              disabled={loading || isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 bg-secondary text-secondary-foreground border border-border/80 rounded-lg text-xs font-medium hover:bg-secondary/80 active:scale-95 transition-all disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {loading && !queueStats ? (
          <div className="flex flex-col items-center justify-center py-20 space-y-4">
            <div className="relative w-12 h-12 flex items-center justify-center">
              <span className="absolute inline-flex h-full w-full rounded-full bg-primary/20 animate-ping" />
              <RefreshCw className="w-8 h-8 text-primary animate-spin" />
            </div>
            <p className="text-sm font-medium text-muted-foreground animate-pulse">
              Querying broker telemetry...
            </p>
          </div>
        ) : (
          <div className="space-y-8 animate-in fade-in duration-500">
            {/* Broker Status Banner */}
            <div className={cn(
              "border rounded-[2rem] p-6 relative overflow-hidden transition-all shadow-xl backdrop-blur-sm",
              queueStats?.available
                ? "bg-emerald-500/5 border-emerald-500/20 shadow-emerald-500/5"
                : "bg-destructive/5 border-destructive/20 shadow-destructive/5"
            )}>
              <div className="flex items-start gap-4">
                <div className={cn(
                  "p-3 rounded-2xl border",
                  queueStats?.available
                    ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20"
                    : "bg-destructive/10 text-destructive border-destructive/20"
                )}>
                  {queueStats?.available ? (
                    <CheckCircle2 className="w-6 h-6 animate-pulse" />
                  ) : (
                    <AlertTriangle className="w-6 h-6 animate-pulse" />
                  )}
                </div>
                <div>
                  <h3 className="text-lg font-black tracking-tight flex items-center gap-2">
                    {queueStats?.available ? "Broker Services Active" : "Broker Offline"}
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {queueStats?.available
                      ? "The RabbitMQ message broker is responsive and actively dispatching tasks to parallel workers."
                      : "Could not connect to RabbitMQ broker. Make sure RABBITMQ_URL is configured correctly."}
                  </p>
                </div>
              </div>
            </div>

            {queueStats?.available && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                {Object.entries(queueStats.queues).map(([name, stat]) => {
                  let title = name;
                  let desc = "";
                  let Icon = Layers;
                  let colorClass = "";
                  let iconColorClass = "";

                  if (name === "scan.quick") {
                    title = "Quick Scan Queue";
                    desc = "In-memory file uploads and rapid syntax evaluations.";
                    Icon = Zap;
                    colorClass = "hover:border-amber-500/50 hover:shadow-amber-500/5";
                    iconColorClass = "bg-amber-500/10 text-amber-500 border-amber-500/20";
                  } else if (name === "scan.repo") {
                    title = "Repository Scan Queue";
                    desc = "Full git repository cloning and deep lifecycle analysis.";
                    Icon = Code;
                    colorClass = "hover:border-indigo-500/50 hover:shadow-indigo-500/5";
                    iconColorClass = "bg-indigo-500/10 text-indigo-500 border-indigo-500/20";
                  } else if (name === "scan.failed") {
                    title = "Dead Letter Queue";
                    desc = "Failed jobs isolated for audit, retry, or debugging.";
                    Icon = AlertTriangle;
                    colorClass = "hover:border-rose-500/50 hover:shadow-rose-500/5";
                    iconColorClass = "bg-rose-500/10 text-rose-500 border-rose-500/20";
                  }

                  return (
                    <Card key={name} className={cn(
                      "group relative overflow-hidden rounded-[2rem] border-border/50 bg-background/50 backdrop-blur-sm transition-all hover:shadow-2xl duration-300",
                      colorClass
                    )}>
                      <CardHeader className="p-8 pb-4">
                        <div className="flex items-center gap-3 mb-4">
                          <div className={cn("p-3 rounded-2xl border", iconColorClass)}>
                            <Icon className="w-5 h-5" />
                          </div>
                          <CardTitle className="text-xl font-black">{title}</CardTitle>
                        </div>
                        <CardDescription className="text-sm text-muted-foreground min-h-[40px]">
                          {desc}
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="px-8 pb-8 space-y-6">
                        <div className="grid grid-cols-3 gap-2 pt-4 border-t border-border/40">
                          <div className="flex flex-col gap-1">
                            <span className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Depth</span>
                            <span className={cn(
                              "text-2xl font-black tracking-tight",
                              stat.depth > 0 ? "text-primary animate-pulse" : "text-foreground"
                            )}>
                              {stat.depth}
                            </span>
                          </div>
                          <div className="flex flex-col gap-1">
                            <span className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Consumers</span>
                            <span className="text-2xl font-black tracking-tight text-foreground">
                              {stat.consumers}
                            </span>
                          </div>
                          <div className="flex flex-col gap-1">
                            <span className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Throughput</span>
                            <span className="text-lg font-black tracking-tight text-emerald-400">
                              {stat.throughput} <span className="text-[9px] font-medium text-muted-foreground">/s</span>
                            </span>
                          </div>
                        </div>

                        {/* A nice visual indicator progress bar */}
                        <div className="w-full bg-secondary/30 rounded-full h-1.5 overflow-hidden">
                          <div
                            className={cn(
                              "h-full rounded-full transition-all duration-500",
                              name === "scan.failed" ? "bg-rose-500" : "bg-primary"
                            )}
                            style={{
                              width: stat.depth > 0 ? `${Math.min(100, (stat.depth / 20) * 100)}%` : "0%"
                            }}
                          />
                        </div>

                        {name === "scan.failed" && (
                          <Button
                            onClick={handleRequeueFailed}
                            disabled={isRequeueing || stat.depth === 0}
                            variant="outline"
                            className={cn(
                              "w-full rounded-2xl h-11 font-bold mt-4 border-rose-500/20 hover:border-rose-500/50 hover:bg-rose-500/10 text-rose-500 transition-all flex items-center justify-center gap-2",
                              stat.depth === 0 && "opacity-50 cursor-not-allowed border-muted hover:bg-transparent text-muted-foreground"
                            )}
                          >
                            {isRequeueing ? (
                              <>
                                <RefreshCw className="w-4 h-4 animate-spin" />
                                Requeueing...
                              </>
                            ) : (
                              <>
                                <RefreshCw className="w-4 h-4" />
                                Requeue Failed Jobs
                              </>
                            )}
                          </Button>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
