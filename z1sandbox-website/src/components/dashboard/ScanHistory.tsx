import { useState, useEffect } from "react";
import { 
  History, 
  Search, 
  Calendar, 
  AlertTriangle, 
  CheckCircle2, 
  Clock, 
  ExternalLink,
  RefreshCw,
  ShieldAlert,
  ChevronRight,
  Database
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface ScanHistoryProps {
  baseUrl: string;
  onViewReport: (jobId: string) => void;
}

interface ScanRecord {
  job_id: string;
  status: string;
  overall_status: string;
  total_findings: number;
  created_at: string;
  tools_used: string;
}

const ScanHistory = ({ baseUrl, onViewReport }: ScanHistoryProps) => {
  const [history, setHistory] = useState<ScanRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchHistory = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(`${baseUrl}/v1/history`);
      if (!response.ok) throw new Error("Failed to fetch history");
      const data = await response.json();
      setHistory(data.scans || []);
    } catch (error) {
      console.error("History fetch error:", error);
      toast.error("Failed to load scan history");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchHistory();
  }, [baseUrl]);

  const formatDate = (isoString: string) => {
    try {
        return new Date(isoString).toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        return isoString;
    }
  };

  return (
    <div className="flex flex-col h-full bg-background/50 backdrop-blur-xl border-l border-white/5 animate-in fade-in slide-in-from-right-4 duration-500">
      <div className="p-6 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-primary/10 border border-primary/20">
            <History className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h2 className="text-xl font-black uppercase tracking-tight text-foreground">Audit History</h2>
            <p className="text-[10px] text-muted-foreground uppercase tracking-widest font-bold opacity-50">Telemetry Logs & Archives</p>
          </div>
        </div>
        <Button 
          variant="ghost" 
          size="icon" 
          onClick={fetchHistory}
          disabled={isLoading}
          className="hover:bg-primary/10 hover:text-primary transition-all duration-300"
        >
          <RefreshCw className={cn("w-4 h-4 text-muted-foreground/60", isLoading && "animate-spin text-primary")} />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-20 space-y-4 opacity-50">
              <RefreshCw className="w-8 h-8 animate-spin text-primary" />
              <span className="text-[10px] font-black uppercase tracking-[0.2em] animate-pulse">Retrieving Archives...</span>
            </div>
          ) : !history || history.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 space-y-6 text-center">
              <div className="p-4 rounded-full bg-muted/10 border border-muted/20">
                <Database className="w-8 h-8 text-muted-foreground opacity-10" />
              </div>
              <div className="space-y-2">
                <h3 className="text-sm font-black uppercase tracking-tight text-foreground/30">No Historical Data</h3>
                <p className="text-[9px] text-muted-foreground font-bold uppercase tracking-widest leading-relaxed max-w-[200px] opacity-40">
                  Your previous security audits will be logged here automatically after execution.
                </p>
              </div>
            </div>
          ) : (
            history.map((scan) => (
              <div 
                key={scan.job_id}
                onClick={() => onViewReport(scan.job_id)}
                className="group relative bg-white/[0.02] border border-white/5 rounded-xl p-4 cursor-pointer hover:bg-white/[0.04] hover:border-primary/30 transition-all duration-300 active:scale-[0.98]"
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-3 flex-1">
                    <div className="flex items-center gap-2">
                      <div className={cn(
                        "p-1.5 rounded-md",
                        scan.overall_status === "CLEAN" 
                          ? "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20" 
                          : "bg-destructive/10 text-destructive border border-destructive/20"
                      )}>
                        {scan.overall_status === "CLEAN" ? <ShieldAlert className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                      </div>
                      <div className="flex flex-col">
                        <span className="text-[10px] font-black uppercase tracking-tight text-foreground/80 group-hover:text-primary transition-colors">
                          {scan.overall_status || "SECURE"} VERDICT
                        </span>
                        <div className="flex items-center gap-2">
                          <Clock className="w-2.5 h-2.5 text-muted-foreground/30" />
                          <span className="text-[8px] font-bold text-muted-foreground/50 uppercase tracking-widest">{formatDate(scan.created_at)}</span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-4 pl-1">
                       <div className="flex flex-col">
                        <span className="text-[8px] font-black text-muted-foreground/40 uppercase tracking-widest mb-1">Total Findings</span>
                        <Badge variant="outline" className={cn(
                          "text-[9px] font-black h-5 border-current/20 px-2",
                          scan.total_findings > 0 ? "text-destructive bg-destructive/5" : "text-emerald-500 bg-emerald-500/5"
                        )}>
                          {scan.total_findings} RISKS
                        </Badge>
                      </div>
                      <div className="w-px h-6 bg-white/5" />
                      <div className="flex flex-col">
                        <span className="text-[8px] font-black text-muted-foreground/40 uppercase tracking-widest mb-1">Job ID</span>
                        <span className="text-[9px] font-mono text-muted-foreground/40 font-bold truncate max-w-[100px]">
                          {scan.job_id.split('-')[0]}...
                        </span>
                      </div>
                    </div>
                  </div>
                  
                  <div className="flex items-center justify-center w-8 h-8 rounded-full bg-white/5 border border-white/5 group-hover:bg-primary group-hover:border-primary transition-all duration-300">
                    <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-primary-foreground transition-colors" />
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
};

export default ScanHistory;
