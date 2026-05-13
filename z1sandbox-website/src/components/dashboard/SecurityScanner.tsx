import { useState } from "react";
import { 
  X, 
  ShieldAlert, 
  Terminal, 
  AlertCircle, 
  RefreshCw,
  ShieldCheck,
  Zap,
  Cpu,
  Bug,
  Layout
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface SecurityScannerProps {
  isOpen: boolean;
  onClose: () => void;
  backend: string;
  baseUrl: string;
  apiKey: string;
}

const SecurityScanner = ({ isOpen, onClose, backend, baseUrl, apiKey }: SecurityScannerProps) => {
  const [code, setCode] = useState("# Simple Code Example\ndef greet(name):\n    return f\"Hello, {name}!\"\n\nprint(greet(\"User\"))");
  const [isScanning, setIsScanning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [status, setStatus] = useState<"READY" | "AUDITING" | "SUCCESS" | "FAILED">("READY");

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

    // 1. Structural JSON Detection (Highest Priority)
    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
      try {
        JSON.parse(text);
        return "json";
      } catch (e) { /* ignore and continue */ }
    }

    // 2. Shebang Detection (Strong Shell indicator)
    if (text.startsWith("#!")) return "sh";

    // --- Scoring Logic ---

    // Python Indicators
    if (/\b(import|from)\s+\w+/.test(text)) scores.py += 10;
    if (/\bdef\s+\w+\(/.test(text)) scores.py += 10;
    if (/\bclass\s+\w+[:\(]/.test(text)) scores.py += 10;
    if (/\bprint\(/.test(text)) scores.py += 5;
    if (/\bif\s+__name__\s*==/.test(text)) scores.py += 20;
    if (/\bself\b/.test(text)) scores.py += 5;
    if (/\basync\s+def\b/.test(text)) scores.py += 10;
    if (/\b(try|except|finally):/m.test(text)) scores.py += 5;

    // YAML & K8s Indicators
    if (text.startsWith("---")) scores.yaml += 15;
    
    const hasApiVersion = /apiVersion:/m.test(text);
    const hasKind = /kind:/m.test(text);
    const isK8s = hasApiVersion && hasKind;
    
    if (isK8s) {
      scores.k8s = (scores.k8s || 0) + 30;
    } else if (hasApiVersion || hasKind || /^(metadata|spec|services|version):/m.test(text)) {
      scores.yaml += 10;
    }
    
    const kvPairs = (text.match(/^\s*[\w.-]+\s*:\s*.+/gm) || []).length;
    scores.yaml += Math.min(kvPairs * 2, 20); // Cap KV pairs contribution

    // JavaScript / TypeScript Indicators
    if (/\b(const|let|var)\s+\w+\s*=/.test(text)) scores.js += 5;
    if (/\bimport\s+.*from\s+['"]/.test(text)) scores.js += 10;
    if (/\bexport\s+(default|const|class|function)\b/.test(text)) scores.js += 10;
    if (/\bconsole\.log\(/.test(text)) scores.js += 5;
    if (/\bfunction\s+\w+\s*\(/.test(text)) scores.js += 5;
    if (/\binterface\s+\w+\s*{/.test(text)) scores.js += 10;
    if (/\b(await|async)\b/.test(text) && !scores.py) scores.js += 5;

    // Go Indicators
    if (/\bpackage\s+\w+/.test(text)) scores.go += 15;
    if (/\bfunc\s+\w+\(/.test(text)) scores.go += 10;
    if (/\btype\s+\w+\s+struct\b/.test(text)) scores.go += 10;

    // Shell Indicators
    if (/\b(sudo|apt-get|yum|export|grep|awk|sed)\b/.test(text)) scores.sh += 5;
    if (/\b(if\s+\[|then|fi|done|do)\b/.test(text)) scores.sh += 10;

    // --- Conflicts & Contextual Adjustments ---
    
    // If it has strong Python keywords, it's very unlikely to be YAML or K8S
    if (scores.py > 5) {
      scores.yaml -= 15;
      scores.k8s -= 15;
    }
    
    // If it has strong JS keywords, it's very unlikely to be YAML or K8S
    if (scores.js > 5) {
      scores.yaml -= 15;
      scores.k8s -= 15;
    }

    // Find the winner
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
      setStatus("AUDITING");
      setResult(null);

      const lang = detectLanguage(code);
      const apiExt = lang === 'k8s' ? 'yaml' : lang;
      const filename = `input.${apiExt}`;

      const response = await fetch(`${baseUrl}/scan-jobs`, {
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

      const normalized = data.report || data;
      setResult({
        ...normalized,
        total_findings: normalized.findings?.length || normalized.summary?.findings_count || 0
      });
      setStatus("SUCCESS");
      toast.success("Security audit completed!");
    } catch (error: any) {
      console.error("Scan error:", error);
      setStatus("FAILED");
      setResult({ error: error.message });
      toast.error("Audit failed check logs");
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[100vw] w-screen h-screen m-0 p-0 overflow-hidden border-none bg-background flex flex-col rounded-none">
        <DialogHeader className="px-12 py-10 border-b bg-muted/30 flex flex-row items-center justify-between space-y-0 shrink-0">
          <div className="flex items-center gap-4">
             <div>
                <DialogTitle className="text-2xl font-black tracking-tight uppercase leading-none">Security Intelligence Ops</DialogTitle>
                <DialogDescription className="text-[11px] font-bold text-muted-foreground uppercase tracking-[0.3em] flex items-center gap-2 mt-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] animate-pulse" />
                  Cluster Node: {backend}
                </DialogDescription>
             </div>
          </div>
        </DialogHeader>

        <div className="flex-1 flex overflow-hidden">
          {/* Side Control Panel */}
          <div className="w-[500px] flex flex-col p-8 bg-muted/20 border-r border-border/50 shrink-0">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <label className="text-[10px] font-black uppercase tracking-[0.2em]">Source Inestion</label>
              <Badge variant="outline" className="rounded-md font-mono text-[9px] font-bold px-2 py-0">
                {detectLanguage(code).toUpperCase()}
              </Badge>
            </div>
            
            <div className="flex-1 relative group rounded-2xl bg-background border border-border overflow-hidden focus-within:ring-2 focus-within:ring-primary/10 transition-all">
              <textarea
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="w-full h-full bg-transparent p-6 font-mono text-[13px] focus:outline-none resize-none leading-relaxed"
                spellCheck="false"
                placeholder="# Paste code..."
              />
            </div>

            <Button 
              onClick={runScan} 
              disabled={isScanning}
              className="mt-6 h-11 rounded-lg bg-primary text-primary-foreground hover:opacity-90 font-bold text-xs uppercase tracking-widest transition-all shadow-lg flex items-center justify-center gap-2 active:scale-[0.98]"
            >
              {isScanning ? (
                <div className="flex items-center gap-2">
                  <LoadingSpinner size="sm" className="text-current" />
                  <span className="uppercase tracking-widest text-[9px]">Analyzing...</span>
                </div>
              ) : (
                <>
                  <Zap className="w-3 h-3 fill-current" />
                  EXECUTE AUDIT
                </>
              )}
            </Button>
          </div>

          {/* Main Content Area */}
          <div className="flex-1 bg-background overflow-hidden flex flex-col">
            <ScrollArea className="flex-1 w-full">
              <div className="p-10 max-w-7xl mx-auto w-full">
                {status === "READY" && (
                    <div className="h-[60vh] flex flex-col items-center justify-center text-center">
                    <div>
                        <h3 className="text-xl font-black uppercase tracking-tight text-foreground/50">Pipeline Disengaged</h3>
                        <p className="text-[10px] text-muted-foreground/30 mt-2 font-bold uppercase tracking-[0.2em]">Ready for target code submission</p>
                    </div>
                    </div>
                )}

                {status === "AUDITING" && (
                    <div className="h-[50vh] flex flex-col items-center justify-center text-center space-y-8 animate-in fade-in zoom-in-95 duration-500">
                        <div className="space-y-4">
                            <h3 className="text-3xl font-black uppercase tracking-tighter">Auditing Security Probe...</h3>
                            <p className="text-muted-foreground font-bold uppercase tracking-widest text-[10px] opacity-60">Provisioning isolated execution sandbox</p>
                        </div>
                        <LoadingSpinner size="lg" className="text-primary/60" />
                    </div>
                )}

                {status === "SUCCESS" && result && (
                    <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
                        {/* Compact Header */}
                        <div className={cn(
                            "px-6 py-3 rounded-lg flex items-center justify-between border shadow-sm",
                            result.summary?.overall_status === "CLEAN" 
                                ? "bg-emerald-500/5 border-emerald-500/20 text-emerald-600 dark:text-emerald-400" 
                                : "bg-destructive/5 border-destructive/20 text-destructive"
                        )}>
                            <div className="flex flex-col">
                                <span className="text-[9px] font-black uppercase tracking-[0.2em] opacity-60">Audit Verdict</span>
                                <div className="flex items-center gap-2 mt-1">
                                    <h2 className="text-xl font-black tracking-tight uppercase">
                                        {result.summary?.overall_status || "SECURE"}
                                    </h2>
                                    <Badge variant="outline" className="border-current/30 text-[8px] uppercase font-black py-0 h-4">{result.total_findings} RISKS</Badge>
                                </div>
                            </div>
                            <div className="flex items-center gap-6 pr-2">
                               <div className="flex items-center gap-3">
                                  <span className="text-[8px] font-black uppercase tracking-widest opacity-40">Probes Active</span>
                                  <span className="text-xl font-black">{result.summary?.total_tools_run ?? 0}</span>
                               </div>
                            </div>
                        </div>

                        {/* Developer View: Terminal First */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 h-[calc(100vh-280px)]">
                           {/* Primary: Full JSON Report */}
                           <section className="flex flex-col gap-4 overflow-hidden">
                              <div className="flex items-center justify-between shrink-0">
                                 <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">
                                    Security Telemetry Report
                                 </h3>
                              </div>
                              <div className="flex-1 bg-zinc-950 rounded-2xl border border-white/5 shadow-2xl overflow-hidden relative group">
                                  <ScrollArea className="h-full w-full overflow-x-auto">
                                     <div className="p-8">
                                        <pre className="text-[12px] font-mono text-emerald-500/70 leading-relaxed whitespace-pre font-medium min-w-max block">
                                           {JSON.stringify(result.report || result, null, 2)}
                                        </pre>
                                     </div>
                                  </ScrollArea>
                              </div>
                           </section>

                           {/* Secondary: Vulnerabilities & Breakdown */}
                           <div className="flex flex-col gap-8 overflow-hidden">
                              <section className="flex flex-col gap-4 h-2/3 overflow-hidden">
                                 <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground shrink-0">Vulnerability Insights</h3>
                                 <ScrollArea className="flex-1 border-t pt-4">
                                    {result.findings && result.findings.length > 0 ? (
                                       <div className="space-y-4 pr-4">
                                          {result.findings.map((f: any, i: number) => {
                                             const severity = (f.severity || "MEDIUM").toUpperCase();
                                             const sevColor = severity === "CRITICAL" ? "bg-red-500/10 text-red-500 border-red-500/20" :
                                                              severity === "HIGH" ? "bg-orange-500/10 text-orange-500 border-orange-500/20" :
                                                              severity === "MEDIUM" ? "bg-amber-500/10 text-amber-500 border-amber-500/20" :
                                                              "bg-blue-500/10 text-blue-500 border-blue-500/20";
                                             
                                             return (
                                                <div key={i} className="p-5 rounded-xl border bg-muted/10 hover:bg-muted/20 transition-all group relative overflow-hidden">
                                                   <div className="flex items-center justify-between mb-3">
                                                      <div className="flex items-center gap-2">
                                                         <Badge variant="outline" className={cn("text-[8px] font-black px-1.5 py-0", sevColor)}>
                                                            {severity}
                                                         </Badge>
                                                         <span className="text-[10px] font-black text-muted-foreground tracking-widest lowercase">{f.tool}</span>
                                                      </div>
                                                      {f.line && <span className="text-[9px] font-mono opacity-40">L:{f.line}</span>}
                                                   </div>
                                                   
                                                   <h4 className="text-sm font-black tracking-tight mb-2 lowercase">{f.issue || "security violation"}</h4>
                                                   
                                                   <div className="flex flex-col gap-2 mt-4">
                                                      <span className="text-[9px] font-black tracking-[0.1em] text-muted-foreground/60 lowercase">remediation insight</span>
                                                      <div className="p-4 rounded-lg bg-background/50 text-[11px] font-medium text-foreground/80 border border-border/40 leading-relaxed italic lowercase">
                                                         {f.remediation || "analyze the specific code structure and apply industry security standards to mitigate this risk."}
                                                      </div>
                                                   </div>

                                                   <div className="mt-3 flex items-center justify-between text-[9px] font-mono opacity-40">
                                                      <span>FILE: {f.file || "unknown"}</span>
                                                   </div>
                                                </div>
                                             );
                                          })}
                                       </div>
                                    ) : (
                                       <div className="h-full flex flex-col items-center justify-center text-center opacity-30">
                                          <span className="text-[10px] font-black uppercase tracking-widest">Integrity Verified</span>
                                       </div>
                                    )}
                                 </ScrollArea>
                              </section>

                              <section className="flex flex-col gap-4 h-1/2 overflow-hidden">
                                 <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground shrink-0">Pipeline Breakdown</h3>
                                 <ScrollArea className="flex-1 border-t pt-4">
                                    <div className="grid gap-3 pr-4 pb-4">
                                       {Object.entries(result.scans || {}).map(([name, scan]: [string, any]) => (
                                          <div key={name} className="p-4 rounded-xl border bg-muted/10 flex items-center justify-between">
                                             <span className="text-[11px] font-black uppercase tracking-wider">{name}</span>
                                             <Badge className={cn(
                                                "text-[8px] font-black uppercase",
                                                scan.exit_code === 0 ? "bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20" : "bg-destructive/10 text-destructive hover:bg-destructive/20"
                                             )}>
                                                {scan.exit_code === 0 ? "PASSED" : "FAILED"}
                                             </Badge>
                                          </div>
                                       ))}
                                    </div>
                                 </ScrollArea>
                              </section>
                           </div>
                        </div>
                    </div>
                )}

                {status === "FAILED" && (
                    <div className="p-16 rounded-[4rem] bg-destructive/10 border-2 border-destructive/20 max-w-3xl mx-auto">
                        <div className="flex items-center gap-6 text-destructive mb-8">
                            <AlertCircle className="w-12 h-12" />
                            <span className="font-black text-2xl uppercase tracking-tighter">Critical Pipeline Fault</span>
                        </div>
                        <p className="text-sm text-destructive font-bold leading-relaxed bg-black/10 p-10 rounded-[2.5rem] font-mono border border-destructive/10">
                            {result?.error || "Fatal exception encountered during the security ingestion phase."}
                        </p>
                        <Button 
                            onClick={() => setStatus("READY")}
                            variant="outline" 
                            className="mt-8 rounded-full border-destructive/20 text-destructive hover:bg-destructive/10 px-8"
                        >
                            RESTART PIPELINE
                        </Button>
                    </div>
                )}
              </div>
            </ScrollArea>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default SecurityScanner;
