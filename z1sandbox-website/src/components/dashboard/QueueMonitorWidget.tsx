import { useState, useEffect } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { Zap, Code, AlertTriangle, CheckCircle2, Layers, RefreshCw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface QueueStat {
  depth: number;
  consumers: number;
  throughput: number;
}

interface QueueStatsResponse {
  available: boolean;
  queues: Record<string, QueueStat>;
}

interface QueueMonitorWidgetProps {
  apiBaseUrl: string;
  activeTab: string;
}

export default function QueueMonitorWidget({ apiBaseUrl, activeTab }: QueueMonitorWidgetProps) {
  const { isAuthenticated, getAccessTokenSilently } = useAuth0();
  const [queueStats, setQueueStats] = useState<QueueStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchQueueStats = async () => {
    try {
      if (!isAuthenticated) return;
      const token = await getAccessTokenSilently();
      const response = await fetch(`${apiBaseUrl}/v1/queue/stats`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setQueueStats(data);
        setLastUpdated(new Date());
      }
    } catch (err) {
      console.error("Error fetching queue stats:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "queues" && isAuthenticated) {
      setLoading(true);
      fetchQueueStats();
      const interval = setInterval(fetchQueueStats, 3000);
      return () => clearInterval(interval);
    }
  }, [activeTab, isAuthenticated]);

  if (loading && !queueStats) {
    return (
      <div className="p-20 flex flex-col items-center justify-center text-center space-y-6">
        <RefreshCw className="w-8 h-8 animate-spin text-primary/40" />
        <p className="text-[10px] text-muted-foreground font-black uppercase tracking-[0.2em] opacity-60 animate-pulse">
          Querying broker telemetry...
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Connection status banner */}
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
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-black tracking-tight flex items-center gap-2">
              {queueStats?.available ? "Broker Services Active" : "Broker Offline"}
            </h3>
            <p className="text-sm text-muted-foreground mt-1">
              {queueStats?.available
                ? "The RabbitMQ message broker is responsive and actively dispatching tasks to parallel workers."
                : "Could not connect to RabbitMQ broker. Make sure RABBITMQ_URL is configured correctly."}
            </p>
          </div>
          {lastUpdated && (
            <span className="text-[10px] text-muted-foreground self-center">
              updated: {lastUpdated.toLocaleTimeString()}
            </span>
          )}
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
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
