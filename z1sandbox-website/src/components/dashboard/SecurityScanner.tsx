import { useState, useEffect } from "react";
import {
  X,
  ShieldCheck,
  Zap,
  AlertCircle,
  FileCode,
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Shield,
  Copy,
  Terminal,
  Code2
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "@/components/ui/resizable";
import { useJobStore } from "@/hooks/useJobStore";
import { UnifiedPipelineView } from "./UnifiedPipelineView";
import { JobsPanel } from "./JobsPanel";
import { InlineApiKeyPanel } from "./InlineApiKeyPanel";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell
} from "recharts";

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

interface SecurityScannerProps {
  isOpen: boolean;
  onClose: () => void;
  backend: string;
  baseUrl: string;
  apiKey: string;
  inline?: boolean;
  onSwitchTab?: (tab: string) => void;
}

const QUICK_SCAN_STEPS = [
  { key: "QUEUED", label: "Job Queued" },
  { key: "PROVISIONING", label: "Provision Sandbox & Ingest" },
  { key: "SCANNING", label: "Security Analysis" },
  { key: "DONE", label: "Scan Completed" },
];

const CODE_TEMPLATES = [
  {
    name: "Python",
    lang: "py",
    icon: "🐍",
    code: `# Python SQL Injection & Unsafe Eval Example
import sqlite3

def get_user_data(username):
    # INSECURE: direct SQL concatenation
    query = f"SELECT * FROM users WHERE name = '{username}'"
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(query)
    return cursor.fetchall()

def execute_config(user_code):
    # DANGEROUS: arbitrary code execution
    return eval(user_code)
`
  },
  {
    name: "JavaScript",
    lang: "js",
    icon: "🟨",
    code: `// JavaScript XSS & Prototype Pollution Example
const express = require('express');
const app = express();

// INSECURE: reflected XSS via query param
app.get('/search', (req, res) => {
  const query = req.query.q;
  res.send('<html><body>Results for: ' + query + '</body></html>');
});

// INSECURE: prototype pollution
function merge(obj, src) {
  for (let key in src) {
    obj[key] = src[key]; // no hasOwnProperty check
  }
}

app.listen(3000);
`
  },
  {
    name: "TypeScript",
    lang: "js",
    icon: "🔷",
    code: `// TypeScript unsafe any & eval example
const express = require('express');

// INSECURE: using any bypasses type safety
function processInput(data: any) {
  // DANGEROUS: eval on user-controlled data
  return eval(data.command);
}

// INSECURE: hardcoded credentials
const DB_PASSWORD: string = "admin1234";
const JWT_SECRET: string = "supersecretkey";

export { processInput, DB_PASSWORD, JWT_SECRET };
`
  },
  {
    name: "Go",
    lang: "go",
    icon: "🐹",
    code: `package main

import (
	"fmt"
	"net/http"
)

// INSECURE: hardcoded sensitive tokens
const AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE" //gitleaks:allow
const SLACK_WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX" //gitleaks:allow

func main() {
	fmt.Println("Starting AWS connection check...")
	http.Post(AWS_WEBHOOK, "application/json", nil)
}
`
  },
  {
    name: "Rust",
    lang: "sh",
    icon: "🦀",
    code: `// Rust unsafe memory & command injection example
use std::process::Command;

// INSECURE: command injection via unsanitized input
fn run_command(user_input: &str) {
    let output = Command::new("sh")
        .arg("-c")
        .arg(user_input) // Never pass user input directly!
        .output()
        .expect("Failed to execute");
    println!("{:?}", output);
}

// INSECURE: unsafe raw pointer dereference
unsafe fn read_arbitrary_memory(ptr: *const u32) -> u32 {
    *ptr
}

fn main() {
    run_command("whoami; cat /etc/passwd");
}
`
  },
  {
    name: "Shell",
    lang: "sh",
    icon: "🐚",
    code: `#!/bin/bash
# Shell Script Command Injection Vulnerability

read -p "Enter server hostname: " hostname

# INSECURE: direct variable expansion in eval/execution
ping -c 3 $hostname
eval "echo Logs processed for host: $hostname"
`
  },
  {
    name: "Kubernetes",
    lang: "k8s",
    icon: "☸️",
    code: `# Insecure Kubernetes Pod Deployment configuration
apiVersion: v1
kind: Pod
metadata:
  name: vulnerable-web-pod
spec:
  containers:
  - name: web-server
    image: nginx:latest
    securityContext:
      # INSECURE: running as privileged container
      privileged: true
      runAsNonRoot: false
    ports:
    - containerPort: 80
`
  },
  {
    name: "Terraform",
    lang: "yaml",
    icon: "🏗️",
    code: `# Terraform IaC Security Issues Example
resource "aws_s3_bucket" "data" {
  bucket = "company-data-bucket"

  # INSECURE: public access not blocked
}

resource "aws_s3_bucket_acl" "data" {
  bucket = aws_s3_bucket.data.id
  # INSECURE: public-read ACL exposes all objects
  acl    = "public-read"
}

resource "aws_security_group_rule" "allow_all" {
  type        = "ingress"
  from_port   = 0
  to_port     = 65535
  protocol    = "tcp"
  # INSECURE: open to the entire internet
  cidr_blocks = ["0.0.0.0/0"]
}
`
  },
];

const SecurityScanner = ({ isOpen, onClose, backend, baseUrl, apiKey, inline = false, onSwitchTab }: SecurityScannerProps) => {
  const [code, setCode] = useState("# Simple Code Example\ndef greet(name):\n    return f\"Hello, {name}!\"\n\nprint(greet(\"User\"))");
  const [isScanning, setIsScanning] = useState(false);

  const getFilename = (lang: string) => {
    switch (lang) {
      case "py": return "main.py";
      case "go": return "main.go";
      case "js": return "index.js";
      case "k8s": return "pod.yaml";
      case "yaml": return "config.yaml";
      case "sh": return "script.sh";
      case "json": return "data.json";
      default: return "snippet.txt";
    }
  };
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "telemetry">("dashboard");

  // Initialize unified hook
  const {
    jobs,
    volatileResults,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    syncFromServer,
  } = useJobStore("quick-scan", baseUrl, apiKey);

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

    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
      try {
        JSON.parse(text);
        return "json";
      } catch (e) { /* ignore */ }
    }

    if (text.startsWith("#!")) return "sh";

    if (/\b(import|from)\s+\w+/.test(text)) scores.py += 10;
    if (/\bdef\s+\w+\(/.test(text)) scores.py += 10;
    if (/\bclass\s+\w+[:\(]/.test(text)) scores.py += 10;
    if (/\bprint\(/.test(text)) scores.py += 5;
    if (/\bif\s+__name__\s*==/.test(text)) scores.py += 20;

    if (text.startsWith("---")) scores.yaml += 15;
    const hasApiVersion = /apiVersion:/m.test(text);
    const hasKind = /kind:/m.test(text);
    if (hasApiVersion && hasKind) {
      scores.k8s += 30;
    } else if (hasApiVersion || hasKind || /^(metadata|spec|services|version):/m.test(text)) {
      scores.yaml += 10;
    }

    if (/\b(const|let|var)\s+\w+\s*=/.test(text)) scores.js += 5;
    if (/\bimport\s+.*from\s+['"]/.test(text)) scores.js += 10;
    if (/\bconsole\.log\(/.test(text)) scores.js += 5;

    if (/\bpackage\s+\w+/.test(text)) scores.go += 15;
    if (/\bfunc\s+\w+\(/.test(text)) scores.go += 10;

    if (/\b(sudo|apt-get|yum|export|grep|awk|sed)\b/.test(text)) scores.sh += 5;

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

      const lang = detectLanguage(code);
      const apiExt = lang === 'k8s' ? 'yaml' : lang;
      const filename = `input.${apiExt}`;

      // POST asynchronously to support SSE streams
      const response = await fetch(`${baseUrl}/scan-jobs?async=true`, {
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

      const { job_id } = data;

      // Add to store
      addJob({
        job_id,
        job_type: "quick-scan",
        status: "QUEUED",
        progress: 5,
        stepMessage: "Job queued",
        eventIndex: 0,
        metadata: {
          files_count: 1,
          submitted_at: new Date().toISOString()
        },
        summary: null,
        result: null,
        submittedAt: new Date().toISOString(),
        completedAt: null
      });

      setSelectedJobId(job_id);
      openStream(job_id, 0);
      // Force immediate sync so any concurrent CLI-triggered jobs surface at once
      syncFromServer();

      toast.success("Security audit pipeline initiated!");
    } catch (error: any) {
      console.error("Scan error:", error);
      toast.error(error.message || "Audit failed to initiate");
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
      const response = await fetch(`${baseUrl}/v1/jobs/${jobId}`, {
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
  const selectedResult = selectedJobId ? volatileResults[selectedJobId] : null;

  const generateSyntheticLanguages = (filesScanned: string[], findings: any[]): Record<string, any> => {
    const languages: Record<string, any> = {};
    const files = filesScanned && filesScanned.length > 0 ? filesScanned : ["input.py"];

    files.forEach(filePath => {
      const ext = filePath.split('.').pop()?.toLowerCase();
      let langName = "Other";
      if (ext === "py") langName = "Python";
      else if (ext === "go") langName = "Go";
      else if (ext === "js" || ext === "jsx") langName = "JavaScript";
      else if (ext === "ts" || ext === "tsx") langName = "TypeScript";
      else if (ext === "sh" || ext === "bash") langName = "Shell";
      else if (ext === "json") langName = "JSON";
      else if (ext === "yaml" || ext === "yml") {
        const hasK8sTool = findings.some(f => {
          const fileVal = f.file || "";
          const toolVal = f.tool || "";
          return fileVal === filePath && ["kubelinter", "kubeconform", "kubescore"].includes(toolVal.toLowerCase());
        });
        langName = hasK8sTool ? "Kubernetes" : "YAML";
      }

      if (!languages[langName]) {
        languages[langName] = {
          language: langName,
          file_count: 0,
          lines_of_code: code.split('\n').length || 0,
          percentage: 0,
          findings: []
        };
      }
      languages[langName].file_count += 1;
    });

    findings.forEach(finding => {
      const filePath = finding.file || "input.py";
      const ext = filePath.split('.').pop()?.toLowerCase();
      let langName = "Other";
      if (ext === "py") langName = "Python";
      else if (ext === "go") langName = "Go";
      else if (ext === "js" || ext === "jsx") langName = "JavaScript";
      else if (ext === "ts" || ext === "tsx") langName = "TypeScript";
      else if (ext === "sh" || ext === "bash") langName = "Shell";
      else if (ext === "json") langName = "JSON";
      else if (ext === "yaml" || ext === "yml") {
        const toolVal = finding.tool || "";
        const isK8s = ["kubelinter", "kubeconform", "kubescore"].includes(toolVal.toLowerCase());
        langName = isK8s ? "Kubernetes" : "YAML";
      }

      if (languages[langName]) {
        languages[langName].findings.push(finding);
      } else {
        languages[langName] = {
          language: langName,
          file_count: 1,
          lines_of_code: code.split('\n').length || 0,
          percentage: 0,
          findings: [finding]
        };
      }
    });

    const totalFiles = files.length || 1;
    Object.keys(languages).forEach(key => {
      languages[key].percentage = parseFloat(((languages[key].file_count / totalFiles) * 100).toFixed(1));
    });

    return languages;
  };

  // Custom renderer for scan result findings
  const renderQuickScanResult = (result: any) => {
    const normalized = result.report || result;
    const findings = normalized.findings || [];
    const filesScanned = normalized.files_scanned || ["input." + detectLanguage(code)];
    const totalFindings = findings.length;

    const syntheticLangs = generateSyntheticLanguages(filesScanned, findings);
    const langEntries = Object.entries(syntheticLangs);

    const chartData = langEntries.map(([lang, r]) => ({
      name: lang,
      value: r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
    }));

    return (
      <div className="space-y-7 animate-in fade-in duration-500">
        {/* Verdict Banner (Flat) */}
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
              {totalFindings === 0 ? "SCAN VERDICT: SECURE" : "VULNERABILITIES DETECTED"}
            </p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Detected by <span className="font-semibold text-violet-500">Unified Ingestion Pipeline</span>
              {" · "}{filesScanned.length} files scanned{" · "}{code.split('\n').length} lines of code
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

        {/* Tab Toggle */}
        <div className="flex bg-muted/30 p-0.5 rounded-lg max-w-xs">
          <button
            type="button"
            onClick={() => setActiveTab("dashboard")}
            className={cn(
              "flex-1 py-1 px-3 text-[10px] font-black uppercase tracking-wider rounded-md transition-all",
              activeTab === "dashboard"
                ? "bg-violet-600 text-white shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Dashboard
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("telemetry")}
            className={cn(
              "flex-1 py-1 px-3 text-[10px] font-black uppercase tracking-wider rounded-md transition-all",
              activeTab === "telemetry"
                ? "bg-violet-600 text-white shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Telemetry
          </button>
        </div>

        {activeTab === "dashboard" ? (
          <div className="space-y-7 animate-in fade-in duration-300">
            {chartData.length > 0 && (
              <div className="p-1 space-y-4">
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
                    const langInfo = syntheticLangs[d.name];
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
                          <span className="text-[10px] text-muted-foreground/60">{langInfo?.file_count || 0} files</span>
                          <span className="font-semibold text-foreground/80">{d.value}%</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="divide-y divide-border/10">
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

                return (
                  <div key={lang} className="py-2.5">
                    <button
                      className="w-full py-3 flex items-center justify-between text-left hover:bg-muted/10 px-2 rounded-xl transition-all group"
                      onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className="w-3 h-3 rounded-sm shrink-0 shadow-sm"
                          style={{ background: LANG_COLORS[i % LANG_COLORS.length] }}
                        />
                        <span className="font-bold text-sm text-foreground">{lang}</span>
                      </div>
                      <div className="flex items-center gap-3 text-right">
                        <div className="flex flex-col items-end gap-1.5">
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground/60">
                            <span>{info.file_count} files</span>
                            <span>·</span>
                            <span>{info.percentage?.toFixed(1)}%</span>
                          </div>
                          <div className="flex gap-1 items-center">
                            {info.findings.length === 0 ? (
                              <Badge className="h-5 px-2 text-[9px] bg-emerald-500/10 hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold rounded-md">
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
                        {info.findings.length > 0 && (
                          <Badge variant="outline" className="text-[9px] font-black bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20 py-0">
                            {info.findings.length}
                          </Badge>
                        )}
                        {expandedLang === lang ? <ChevronUp className="w-4 h-4 text-muted-foreground/60" /> : <ChevronDown className="w-4 h-4 text-muted-foreground/60" />}
                      </div>
                    </button>

                    {expandedLang === lang && (
                      <div className="mt-3 pl-6 pr-2 space-y-3">
                        {info.findings.length === 0 ? (
                          <div className="py-4 text-xs font-semibold text-muted-foreground/50 text-center">
                            No security findings for this language
                          </div>
                        ) : (
                          <div className="space-y-3 max-h-[350px] overflow-y-auto pr-1">
                            {info.findings.map((f: any, fi: number) => {
                              const sev = f.severity?.toUpperCase() ?? "INFO";
                              const borderLeftColor =
                                sev === "CRITICAL" ? "border-l-red-500 bg-red-500/[0.03]" :
                                sev === "HIGH"     ? "border-l-orange-500 bg-orange-500/[0.03]" :
                                sev === "MEDIUM"   ? "border-l-yellow-500 bg-yellow-500/[0.03]" :
                                sev === "LOW"      ? "border-l-blue-500 bg-blue-500/[0.03]" :
                                                     "border-l-muted-foreground/30 bg-muted/[0.02]";
                              const badgeColor =
                                sev === "CRITICAL" ? "bg-red-500/10 text-red-600 dark:text-red-400" :
                                sev === "HIGH"     ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" :
                                sev === "MEDIUM"   ? "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400" :
                                sev === "LOW"      ? "bg-blue-500/10 text-blue-600 dark:text-blue-400" :
                                                     "bg-muted/30 text-muted-foreground";
                              return (
                                <div
                                  key={fi}
                                  className={cn(
                                    "p-3.5 pl-4 border-l-2 border-y-0 border-r-0 rounded-r-xl text-[11px] transition-all flex flex-col gap-1.5",
                                    borderLeftColor
                                  )}
                                >
                                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                                    <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide px-1.5 py-0 border-0", badgeColor)}>
                                      {sev}
                                    </Badge>
                                    <span className="text-[9px] font-bold text-muted-foreground/60">{f.tool}</span>
                                    {f.line && (
                                      <span className="text-[9px] font-mono text-muted-foreground/45 ml-auto">L:{f.line}</span>
                                    )}
                                  </div>
                                  <p className="text-[11px] font-semibold leading-normal">{f.issue}</p>
                                  {f.file && (
                                    <div className="flex items-center gap-1.5 mt-1">
                                      <FileCode className="w-3 h-3 text-muted-foreground/40 shrink-0" />
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
        ) : (
          /* Developer split view: Report vs Insights (Flat) */
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 h-[700px] animate-in fade-in duration-300">
            {/* JSON Telemetry */}
            <section className="flex flex-col gap-3 overflow-hidden">
              <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">
                Security Telemetry Report
              </h3>
              <div className="flex-1 bg-zinc-950/80 rounded-2xl border border-border/30 overflow-hidden">
                <ScrollArea className="h-full">
                  <pre className="p-6 text-[11px] font-mono text-emerald-500/80 leading-relaxed whitespace-pre font-medium">
                    {JSON.stringify(normalized, null, 2)}
                  </pre>
                </ScrollArea>
              </div>
            </section>

            {/* Vulnerability Breakdown */}
            <div className="flex flex-col gap-6 overflow-hidden">
              <section className="flex flex-col gap-3 h-full overflow-hidden">
                <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground shrink-0">
                  Vulnerability Insights
                </h3>
                <ScrollArea className="flex-1 pr-1">
                  {findings.length > 0 ? (
                    <div className="space-y-4">
                      {findings.map((f: any, i: number) => {
                        const severity = (f.severity || "MEDIUM").toUpperCase();
                        const borderLeftColor =
                          severity === "CRITICAL" ? "border-l-red-500 bg-red-500/[0.03]" :
                          severity === "HIGH"     ? "border-l-orange-500 bg-orange-500/[0.03]" :
                          severity === "MEDIUM"   ? "border-l-yellow-500 bg-yellow-500/[0.03]" :
                          severity === "LOW"      ? "border-l-blue-500 bg-blue-500/[0.03]" :
                                                    "border-l-muted-foreground/30 bg-muted/[0.02]";
                        const badgeColor =
                          severity === "CRITICAL" ? "bg-red-500/10 text-red-600 dark:text-red-400" :
                          severity === "HIGH"     ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" :
                          severity === "MEDIUM"   ? "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400" :
                          severity === "LOW"      ? "bg-blue-500/10 text-blue-600 dark:text-blue-400" :
                                                    "bg-muted/30 text-muted-foreground";

                        return (
                          <div
                            key={i}
                            className={cn(
                              "p-4 border-l-2 border-y-0 border-r-0 rounded-r-xl text-[11px] transition-all flex flex-col gap-2 relative overflow-hidden",
                              borderLeftColor
                            )}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className={cn("text-[8px] font-black px-1.5 py-0 border-0", badgeColor)}>
                                  {severity}
                                </Badge>
                                <span className="text-[9px] font-mono text-muted-foreground tracking-widest uppercase">
                                  {f.tool}
                                </span>
                              </div>
                              {f.line && <span className="text-[9px] font-mono opacity-40">L:{f.line}</span>}
                            </div>

                            <h4 className="text-xs font-bold text-foreground">
                              {f.issue || "Security violation"}
                            </h4>

                            <div className="flex flex-col gap-1.5 mt-2">
                              <span className="text-[8px] font-black tracking-[0.1em] text-muted-foreground/60 uppercase">
                                remediation insight
                              </span>
                              <div className="text-[10px] text-muted-foreground/80 leading-relaxed italic pl-3 border-l border-border/30">
                                {f.remediation || "Analyze the specific code structure and apply industry security standards to mitigate this risk."}
                              </div>
                            </div>

                            <div className="mt-1 text-[8px] font-mono opacity-40">
                              FILE: {f.file || "unknown"}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-center opacity-30 py-20">
                      <span className="text-[10px] font-black uppercase tracking-widest">Integrity Verified</span>
                    </div>
                  )}
                </ScrollArea>
              </section>
            </div>
          </div>
        )}
      </div>
    );
  };

  const bodyContent = (
    <div className="flex-1 overflow-y-auto p-6 sm:p-10 w-full max-w-7xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-[460px_1fr] gap-8 items-start">

        {/* Left Column (Input & Status Stepper) */}
        <div className="lg:sticky lg:top-0 flex flex-col gap-6">
          <div className="rounded-[2rem] border border-border/50 bg-background/50 backdrop-blur-sm p-8 flex flex-col gap-5 shadow-xl shadow-primary/5">
            <div className="flex flex-col gap-4">
              {/* Quick Templates */}
              <div className="flex flex-col gap-2">
                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block">
                  Quick Templates
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {CODE_TEMPLATES.map((tmpl) => (
                    <button
                      key={tmpl.name}
                      type="button"
                      disabled={isScanning}
                      onClick={() => setCode(tmpl.code)}
                      className={cn(
                        "px-2.5 py-1.5 rounded-lg border border-border/50 bg-background/50 hover:bg-secondary/40 text-[10px] font-bold text-muted-foreground hover:text-foreground transition-all flex items-center gap-1.5",
                        detectLanguage(code) === tmpl.lang ? "border-violet-500/30 bg-violet-500/5 text-violet-500" : ""
                      )}
                    >
                      <span className="text-xs">{tmpl.icon}</span>
                      {tmpl.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Source Ingestion Header / IDE Window mockup */}
              <div className="flex flex-col gap-1.5 mt-2">
                <div className="flex items-center justify-between text-muted-foreground">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block">
                    Source Ingestion
                  </label>
                </div>

                <div className="relative h-[380px] rounded-2xl bg-[#0b0e14] border border-border/60 overflow-hidden flex flex-col focus-within:ring-2 focus-within:ring-violet-500/25 transition-all shadow-lg shadow-black/10">
                  {/* Editor Window Header Tab */}
                  <div className="flex items-center justify-between px-4 py-2.5 bg-[#111622] border-b border-border/30 select-none">
                    <div className="flex items-center gap-3">
                      <div className="flex gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f56]" />
                        <span className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" />
                        <span className="w-2.5 h-2.5 rounded-full bg-[#27c93f]" />
                      </div>
                      <div className="h-3 w-px bg-zinc-800 mx-1" />
                      <span className="font-mono text-[11px] text-zinc-400 flex items-center gap-1.5">
                        <FileCode className="w-3.5 h-3.5 text-violet-400" />
                        {getFilename(detectLanguage(code))}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-[9px] font-black tracking-wider uppercase text-zinc-500 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">
                        {detectLanguage(code).toUpperCase()}
                      </span>
                    </div>
                  </div>

                  {/* Editor Body */}
                  <textarea
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    className="flex-1 bg-transparent p-5 font-mono text-xs text-zinc-100 focus:outline-none resize-none leading-relaxed focus:ring-0 overflow-y-auto selection:bg-violet-500/30 caret-violet-500"
                    spellCheck="false"
                    placeholder="# Paste code here..."
                    disabled={isScanning}
                  />
                </div>
              </div>

              <Button
                onClick={runScan}
                disabled={isScanning || !code.trim()}
                className="h-9 w-full rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-bold text-[11px] flex items-center justify-center gap-1.5 shadow-md shadow-violet-600/10 transition-all active:scale-[0.98] mt-2 uppercase tracking-wider"
              >
                {isScanning ? (
                  <>
                    <LoadingSpinner size="sm" className="text-current" />
                    <span>Scanning Snippet...</span>
                  </>
                ) : (
                  <>
                    <Zap className="w-3.5 h-3.5 fill-current" />
                    EXECUTE AUDIT
                  </>
                )}
              </Button>

              {selectedJob && (
                <div className="border-t border-border/10 pt-5 mt-2 flex flex-col gap-4 animate-in fade-in slide-in-from-top-3 duration-300">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground">Pipeline Status</label>
                  <div className="flex flex-col gap-1">
                    {QUICK_SCAN_STEPS.map((step, i) => {
                      const stepIdx = QUICK_SCAN_STEPS.findIndex(s => s.key === selectedJob.status);
                      const isError = selectedJob.status === "ERROR";
                      const isDone = selectedJob.status === "DONE" ? true : i < stepIdx;
                      const isActive = step.key === selectedJob.status && !isError;
                      return (
                         <div key={step.key} className={cn("flex items-center gap-3 py-2 px-3 rounded-xl transition-all", isActive ? "bg-violet-500/8" : "")}>
                           <div className={cn("w-6 h-6 rounded-full flex items-center justify-center shrink-0 border-2 transition-all",
                             isError && i >= stepIdx ? "border-destructive/30 text-destructive/30" :
                               isDone ? "border-emerald-500 bg-emerald-500/10 text-emerald-500" :
                                 isActive ? "border-violet-500 bg-violet-500/10 text-violet-500" :
                                   "border-border text-muted-foreground/30")}>
                             {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <span>{i + 1}</span>}
                           </div>
                           <span className={cn("text-xs font-semibold", isDone ? "text-emerald-500" : isActive ? "text-foreground" : "text-muted-foreground/40")}>
                             {step.label}
                           </span>
                         </div>
                      );
                    })}
                  </div>
                  <div className="h-1.5 rounded-full bg-muted/50 overflow-hidden">
                    <div className={cn("h-full rounded-full transition-all duration-700 ease-out", selectedJob.status === "ERROR" ? "bg-destructive" : "bg-violet-500")} style={{ width: `${selectedJob.progress}%` }} />
                  </div>
                  {selectedJob.stepMessage && <p className="text-[11px] text-muted-foreground">{selectedJob.stepMessage}</p>}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column (Results & Telemetry Stream) */}
        <div className="min-h-[620px]">
          {/* Ready state */}
          {!selectedJob && (
            <div className="h-[600px] rounded-[2.5rem] border border-border/40 bg-card/10 backdrop-blur-sm p-8 flex flex-col justify-between overflow-hidden shadow-inner relative animate-in fade-in duration-500">
              {/* Decorative top-right glow */}
              <div className="absolute -top-24 -right-24 w-48 h-48 rounded-full bg-violet-500/10 blur-3xl" />

              {/* Top Section: Header */}
              <div className="flex flex-col items-center text-center mt-12">
                <div className="p-4 rounded-3xl bg-violet-500/10 border border-violet-500/20 text-violet-500 mb-5 relative">
                  <div className="absolute inset-0 rounded-3xl bg-violet-500/5 animate-ping" />
                  <ShieldCheck className="w-10 h-10 relative z-10" />
                </div>
                <h3 className="font-display font-black text-2xl tracking-tight text-foreground">
                  Security Sandbox Environment
                </h3>
                <p className="text-sm text-muted-foreground mt-2 max-w-md leading-relaxed">
                  Submit code snippets to trigger isolated Kubernetes sandbox workloads for static analysis, secret checking, and dependency verification.
                </p>
              </div>

              {/* Middle Section: Feature Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 my-8">
                <div className="p-5 rounded-2xl border border-border/40 bg-background/50 flex flex-col gap-3">
                  <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 w-fit border border-emerald-500/20">
                    <Activity className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-xs font-black uppercase tracking-wider text-foreground">Isolated Sandboxes</h4>
                    <p className="text-[11px] text-muted-foreground mt-1 leading-normal">
                      Every code audit runs in a dedicated micro-pod sandbox.
                    </p>
                  </div>
                </div>

                <div className="p-5 rounded-2xl border border-border/40 bg-background/50 flex flex-col gap-3">
                  <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-500 w-fit border border-indigo-500/20">
                    <FileCode className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-xs font-black uppercase tracking-wider text-foreground">Multi-Language</h4>
                    <p className="text-[11px] text-muted-foreground mt-1 leading-normal">
                      Auto-detects Python, Go, JavaScript, YAML, and Kubernetes resource files.
                    </p>
                  </div>
                </div>

                <div className="p-5 rounded-2xl border border-border/40 bg-background/50 flex flex-col gap-3">
                  <div className="p-2 rounded-lg bg-amber-500/10 text-amber-500 w-fit border border-amber-500/20">
                    <Zap className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-xs font-black uppercase tracking-wider text-foreground">Deep Inspections</h4>
                    <p className="text-[11px] text-muted-foreground mt-1 leading-normal">
                      Leverages Bandit, GoSec, ESLint, KubeLinter, and regex-based secret scans.
                    </p>
                  </div>
                </div>
              </div>

              {/* Bottom Section: Footer/Status */}
              <div className="border-t border-border/30 pt-5 flex items-center justify-between text-muted-foreground/60 text-[10px] font-bold uppercase tracking-widest mt-auto">
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] animate-pulse" />
                  Scanner Engine Active
                </div>
                <div>
                  v1.2.0-Alpha
                </div>
              </div>
            </div>
          )}

          {/* Ingress / Scanning active state */}
          {selectedJob && selectedJob.status !== "DONE" && selectedJob.status !== "ERROR" && (
            <div className="max-w-5xl mx-auto w-full">
              <UnifiedPipelineView
                job={selectedJob}
                steps={QUICK_SCAN_STEPS}
                result={selectedResult}
                onResultRender={renderQuickScanResult}
                onCancel={handleCancelJob}
              />
            </div>
          )}

          {/* Error state */}
          {selectedJob && selectedJob.status === "ERROR" && (
            <div className="rounded-[2rem] border-2 border-destructive/20 bg-destructive/5 p-10 flex flex-col gap-6">
              <div className="flex items-center gap-4 text-destructive">
                <AlertCircle className="w-10 h-10" />
                <h2 className="text-2xl font-black tracking-tight">Scan Failed</h2>
              </div>
              <p className="font-mono text-sm text-destructive/80 bg-black/5 rounded-2xl p-6 border border-destructive/10 leading-relaxed">
                {selectedJob.stepMessage || "An unexpected error occurred during sandbox execution."}
              </p>
            </div>
          )}

          {/* Finished / Done state */}
          {selectedJob && selectedJob.status === "DONE" && selectedResult && (
            <div className="max-w-5xl mx-auto w-full">
              {renderQuickScanResult(selectedResult)}
            </div>
          )}
        </div>

      </div>
    </div>
  );

  if (inline) {
    return (
      <div className="w-full h-[650px] border-t border-b border-border/30 flex flex-row overflow-hidden p-0 animate-in fade-in duration-500">

        {/* ── Column 1: Code Editor (18%) ── */}
        <div className="w-full md:w-[18%] shrink-0 border-r border-border/30 flex flex-col h-full overflow-hidden">
          <div className="p-4 space-y-3 h-full flex flex-col">
            {/* Quick Templates */}
            <div className="flex flex-col gap-2 shrink-0">
              <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75 block">
                Quick Templates
              </span>
              <div className="flex flex-wrap gap-1.5">
                {CODE_TEMPLATES.map((tmpl) => (
                  <button
                    key={tmpl.name}
                    type="button"
                    disabled={isScanning}
                    onClick={() => setCode(tmpl.code)}
                    className={cn(
                      "px-2 py-1 rounded-md border text-[9px] font-bold transition-all flex items-center gap-1",
                      "border-border/60 bg-secondary/30 text-foreground/70 hover:bg-secondary/60 hover:text-foreground hover:border-border",
                      detectLanguage(code) === tmpl.lang && code === tmpl.code
                        ? "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-400"
                        : ""
                    )}
                  >
                    <span className="text-[10px]">{tmpl.icon}</span>
                    {tmpl.name}
                  </button>
                ))}
              </div>
            </div>

            {/* Source Ingestion */}
            <div className="flex-grow flex flex-col gap-1.5 min-h-0">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75 block shrink-0">
                Source Ingestion
              </label>

              <div className="flex-grow relative rounded-lg border border-border/60 overflow-hidden flex flex-col focus-within:ring-2 focus-within:ring-violet-500/25 transition-all bg-white dark:bg-[#0b0e14]">
                {/* Editor Tab Bar */}
                <div className="flex items-center justify-between px-3 py-2 bg-gray-100 dark:bg-[#111622] border-b border-border/30 select-none shrink-0">
                  <div className="flex items-center gap-2">
                    <div className="flex gap-1">
                      <span className="w-2 h-2 rounded-full bg-[#ff5f56]" />
                      <span className="w-2 h-2 rounded-full bg-[#ffbd2e]" />
                      <span className="w-2 h-2 rounded-full bg-[#27c93f]" />
                    </div>
                    <span className="font-mono text-[10px] text-muted-foreground/70 flex items-center gap-1">
                      <FileCode className="w-3 h-3 text-violet-400" />
                      {getFilename(detectLanguage(code))}
                    </span>
                  </div>
                  <span className="font-mono text-[8px] font-black uppercase text-muted-foreground/50 bg-muted/40 px-1 py-0.5 rounded border border-border/40">
                    {detectLanguage(code).toUpperCase()}
                  </span>
                </div>

                {/* Code textarea */}
                <textarea
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className="flex-grow bg-transparent p-3 font-mono text-[11px] text-foreground focus:outline-none resize-none leading-relaxed overflow-y-auto selection:bg-violet-500/30 caret-violet-500"
                  spellCheck="false"
                  placeholder="# Paste code here..."
                  disabled={isScanning}
                />
              </div>
            </div>

            <Button
              onClick={runScan}
              disabled={isScanning || !code.trim()}
              className="h-8 w-full rounded-md bg-violet-600 hover:bg-violet-500 text-white font-bold text-[10px] flex items-center justify-center gap-1.5 shadow-md shadow-violet-600/10 transition-all active:scale-[0.98] shrink-0 uppercase tracking-wider"
            >
              {isScanning ? (
                <>
                  <LoadingSpinner size="sm" className="text-current" />
                  <span>Scanning...</span>
                </>
              ) : (
                <>
                  <Zap className="w-3 h-3 fill-current" />
                  Execute Audit
                </>
              )}
            </Button>
          </div>
        </div>

        {/* ── Column 2: Jobs List (20%) ── */}
        <div className="w-full md:w-[20%] shrink-0 border-r border-border/30 flex flex-col h-full overflow-hidden">
          <div className="px-3 py-2.5 border-b border-border/20 shrink-0 flex items-center justify-between">
            <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
              Quick Scans
            </span>
            <span className="text-[9px] text-muted-foreground/50 font-semibold uppercase">
              ({jobs.length})
            </span>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            <JobsPanel
              jobs={jobs}
              selectedJobId={selectedJobId}
              onSelectJob={setSelectedJobId}
              onDeleteJob={removeJob}
              jobType="quick-scan"
              embedded={true}
            />
          </div>
        </div>

        {/* ── Column 3: Results Panel (58%) ── */}
        <div className="flex-grow flex flex-col h-full overflow-y-auto p-5 bg-background/5">
          {/* Ready state – no job selected */}
          {!selectedJob && (
            <div className="h-full flex flex-col items-center justify-center gap-6 animate-in fade-in duration-500 text-center px-4">
              <div className="p-4 rounded-2xl bg-violet-500/10 border border-violet-500/15 text-violet-500">
                <ShieldCheck className="w-9 h-9" />
              </div>
              <div>
                <h3 className="font-black text-lg tracking-tight text-foreground">Security Sandbox Environment</h3>
                <p className="text-sm text-muted-foreground mt-1 max-w-sm leading-relaxed">
                  Paste code, pick a template, and hit Execute Audit to trigger an isolated security analysis.
                </p>
              </div>
              <div className="grid grid-cols-3 gap-3 w-full max-w-lg">
                {[
                  { icon: <Activity className="w-3.5 h-3.5" />, color: "emerald", title: "Isolated Sandboxes" },
                  { icon: <FileCode className="w-3.5 h-3.5" />, color: "indigo", title: "Multi-Language" },
                  { icon: <Zap className="w-3.5 h-3.5" />, color: "amber", title: "Deep Inspections" },
                ].map((card) => (
                  <div key={card.title} className="flex flex-col items-center gap-2 p-3 rounded-lg border border-border/30 bg-muted/10">
                    <div className={`p-1.5 rounded-md bg-${card.color}-500/10 text-${card.color}-500`}>{card.icon}</div>
                    <span className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">{card.title}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Scanning / in-progress state */}
          {selectedJob && selectedJob.status !== "DONE" && selectedJob.status !== "ERROR" && (
            <div className="max-w-5xl mx-auto w-full">
              <UnifiedPipelineView
                job={selectedJob}
                steps={QUICK_SCAN_STEPS}
                result={selectedResult}
                onResultRender={renderQuickScanResult}
                onCancel={handleCancelJob}
              />
            </div>
          )}

          {/* Error state */}
          {selectedJob && selectedJob.status === "ERROR" && (
            <div className="rounded-xl border-2 border-destructive/20 bg-destructive/5 p-8 flex flex-col gap-5">
              <div className="flex items-center gap-3 text-destructive">
                <AlertCircle className="w-8 h-8" />
                <h2 className="text-xl font-black tracking-tight">Scan Failed</h2>
              </div>
              <p className="font-mono text-sm text-destructive/80 bg-black/5 rounded-xl p-5 border border-destructive/10 leading-relaxed">
                {selectedJob.stepMessage || "An unexpected error occurred during sandbox execution."}
              </p>
            </div>
          )}

          {/* Done state */}
          {selectedJob && selectedJob.status === "DONE" && selectedResult && (
            <div className="max-w-5xl mx-auto w-full">
              {renderQuickScanResult(selectedResult)}
            </div>
          )}

          {/* Done state but result still loading */}
          {selectedJob && selectedJob.status === "DONE" && !selectedResult && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground/50">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="text-xs font-semibold">Loading scan results…</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[1240px] w-[95vw] h-[90vh] rounded-3xl border border-border bg-background flex flex-col overflow-hidden p-0 shadow-2xl">

        {/* Top Header */}
        <DialogHeader className="px-8 py-5 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
          <div className="flex items-center gap-4">
            <div className="p-3 rounded-2xl bg-violet-500/10 border border-violet-500/20 text-violet-500 shrink-0">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <DialogTitle className="text-2xl font-display font-black tracking-tight text-foreground">
                Quick Security Scanner
              </DialogTitle>
              <DialogDescription className="text-[10px] font-bold text-muted-foreground uppercase tracking-[0.25em] flex items-center gap-2 mt-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] animate-pulse" />
                Cluster Node: {backend}
              </DialogDescription>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl hover:bg-muted/50 transition-colors text-muted-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </DialogHeader>

        {bodyContent}

      </DialogContent>
    </Dialog>
  );
};

export default SecurityScanner;
