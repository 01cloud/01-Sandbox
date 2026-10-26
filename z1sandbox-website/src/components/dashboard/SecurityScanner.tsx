import { useState, useEffect, useMemo, useRef } from "react";
import {
  X,
  ShieldCheck,
  Zap,
  AlertCircle,
  FileCode,
  Activity,
  CheckCircle2,
  Loader2,
  Shield,
} from "lucide-react";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useJobStore } from "@/hooks/useJobStore";
import { resolveBackendUrl } from "@/lib/apiConfig";
import { UnifiedPipelineView } from "./UnifiedPipelineView";
import { JobsPanel } from "./JobsPanel";

const LANG_COLORS = [
  "#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#14b8a6", "#84cc16", "#f97316",
];

// Full class strings so Tailwind can see them at build time
const COLOR_CLASSES: Record<string, string> = {
  emerald: "bg-emerald-500/10 text-emerald-500",
  indigo: "bg-indigo-500/10 text-indigo-500",
  amber: "bg-amber-500/10 text-amber-500",
};

interface SecurityScannerProps {
  isOpen: boolean;
  onClose: () => void;
  backend: string;
  baseUrl: string;
  apiKey: string;
  authToken?: string;
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
    icon: "/logos/python.svg",
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
    icon: "/logos/javascript.svg",
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
    lang: "ts",
    icon: "/logos/typescript.svg",
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
    icon: "/logos/go-icon.svg",
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
	fmt.Println(AWS_ACCESS_KEY)
	http.Post(SLACK_WEBHOOK, "application/json", nil)
}
`
  },
  {
    name: "Rust",
    lang: "rs",
    icon: "/logos/rust.svg",
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
    icon: "/logos/bash.svg",
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
    icon: "/logos/kubernetes.svg",
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
    lang: "terraform",
    icon: "/logos/terraform.svg",
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

/* ──────────────────────────────────────────────────────────────
 * Helpers (module-level, pure)
 * ────────────────────────────────────────────────────────────── */

const isDoneStatus = (status?: string | null) => status === "DONE" || status === "COMPLETE";

/**
 * Read a fetch Response body without ever throwing on non-JSON content.
 * Falls back to parsing only the first line (handles NDJSON / SSE-style bodies).
 */
async function readBody(response: Response): Promise<{ data: any; raw: string; isJson: boolean }> {
  const raw = await response.text();
  if (!raw.trim()) return { data: null, raw, isJson: true };

  try {
    return { data: JSON.parse(raw), raw, isJson: true };
  } catch {
    /* fall through */
  }

  const firstLine = raw.split("\n").find((l) => l.trim());
  if (firstLine) {
    try {
      return { data: JSON.parse(firstLine.replace(/^data:\s*/, "")), raw, isJson: true };
    } catch {
      /* fall through */
    }
  }

  console.error("Non-JSON response body:", {
    url: response.url,
    status: response.status,
    contentType: response.headers.get("content-type"),
    body: raw.slice(0, 500),
  });
  return { data: null, raw, isJson: false };
}

/** Build a readable error message from a failed response. */
function errorMessageFrom(response: Response, data: any, raw: string, fallback: string): string {
  const detail = data?.detail ?? data?.error ?? data?.message;
  if (typeof detail === "string" && detail) return detail;
  if (detail) {
    try {
      return JSON.stringify(detail).slice(0, 200);
    } catch {
      /* ignore */
    }
  }
  const text = raw.trim();
  if (text && !text.startsWith("<")) return `${fallback} (HTTP ${response.status}): ${text.slice(0, 120)}`;
  return `${fallback} (HTTP ${response.status})`;
}

const detectLanguage = (code: string) => {
  const text = code.trim();
  if (!text) return "py";

  const scores: Record<string, number> = {
    py: 0,
    yaml: 0,
    k8s: 0,
    js: 0,
    ts: 0,
    go: 0,
    rs: 0,
    sh: 0,
    terraform: 0,
  };

  if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
    try {
      JSON.parse(text);
      return "json";
    } catch (e) { /* ignore */ }
  }

  if (text.startsWith("#!")) return "sh";

  // Terraform / HCL
  if (
    /^[ \t]*(?:resource|provider|variable|output|module|data)\s+"[^"]+"/m.test(text) ||
    /^[ \t]*terraform\s*\{/m.test(text) ||
    /^[ \t]*(?:resource|provider|variable|output|module)\s+[\w"-]+/m.test(text)
  ) {
    scores.terraform += 25;
  }

  // Python
  if (/\b(import|from)\s+\w+/.test(text)) scores.py += 10;
  if (/\bdef\s+\w+\s*\(/.test(text)) scores.py += 15;
  if (/\bclass\s+\w+[:\(]/.test(text)) scores.py += 10;
  if (/\bprint\(/.test(text)) scores.py += 5;
  if (/\bif\s+__name__\s*==/.test(text)) scores.py += 20;

  // YAML / Kubernetes
  if (text.startsWith("---")) scores.yaml += 15;
  const hasApiVersion = /apiVersion:/m.test(text);
  const hasKind = /kind:/m.test(text);
  if (hasApiVersion && hasKind) {
    scores.k8s += 30;
  } else if (hasApiVersion || hasKind || /^(metadata|spec|services|version):/m.test(text)) {
    scores.yaml += 10;
  }

  // JS & TS shared patterns
  if (/\b(const|let|var)\s+\w+\s*=/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\brequire\s*\(\s*['"]/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\bimport\s+.*from\s+['"]/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\bexport\s+(default|const|let|function|class|\{)/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bfunction\s+\w+\s*\(/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bconsole\.(log|error|warn|info)\(/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bapp\.(get|post|put|delete|listen|use)\(/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\b(req|res|next)\b/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/=>\s*\{?/.test(text)) { scores.js += 5; scores.ts += 5; }

  // TypeScript specific
  if (/:\s*(any|string|number|boolean|void|never|unknown|object|\[\])\b/.test(text)) scores.ts += 25;
  if (/\binterface\s+\w+\s*\{/.test(text)) scores.ts += 20;
  if (/\btype\s+\w+\s*=/.test(text)) scores.ts += 20;
  if (/\bas\s+(any|string|number|unknown)\b/.test(text)) scores.ts += 15;

  // Go
  if (/\bpackage\s+\w+/.test(text)) scores.go += 15;
  if (/\bfunc\s+\w+\s*\(/.test(text)) scores.go += 10;

  // Rust
  if (/\bfn\s+\w+\s*\(/.test(text)) scores.rs += 15;
  if (/\buse\s+std::/.test(text)) scores.rs += 15;
  if (/\blet\s+mut\s+\w+/.test(text)) scores.rs += 15;
  if (/\bprintln!/.test(text)) scores.rs += 10;
  if (/\bunsafe\s+(fn|block|\{)/.test(text)) scores.rs += 15;
  if (/\bCommand::new\(/.test(text)) scores.rs += 15;

  // Shell
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

const getFilename = (lang: string) => {
  switch (lang) {
    case "py": return "main.py";
    case "go": return "main.go";
    case "rs":
    case "rust": return "main.rs";
    case "js": return "index.js";
    case "ts":
    case "typescript": return "index.ts";
    case "k8s": return "pod.yaml";
    case "yaml": return "config.yaml";
    case "terraform":
    case "tf": return "main.tf";
    case "sh": return "script.sh";
    case "json": return "data.json";
    default: return "snippet.txt";
  }
};

const K8S_TOOLS = ["kubelinter", "kubeconform", "kubescore"];

const languageFromPath = (filePath: string, tool = ""): string => {
  const ext = filePath.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "py": return "Python";
    case "go": return "Go";
    case "rs": return "Rust";
    case "js":
    case "jsx": return "JavaScript";
    case "ts":
    case "tsx": return "TypeScript";
    case "sh":
    case "bash": return "Shell";
    case "json": return "JSON";
    case "tf":
    case "tfvars":
    case "terraform": return "Terraform";
    case "yaml":
    case "yml": return K8S_TOOLS.includes(tool.toLowerCase()) ? "Kubernetes" : "YAML";
    default: return "Other";
  }
};

const severityStyles = (sev: string) => {
  switch (sev) {
    case "CRITICAL":
      return {
        card: "border-red-500/30 bg-red-500/[0.03] text-red-900 dark:text-red-200",
        left: "border-l-red-500 bg-red-500/[0.03]",
        badge: "bg-red-500/10 text-red-600 dark:text-red-400",
      };
    case "HIGH":
      return {
        card: "border-orange-500/30 bg-orange-500/[0.03] text-orange-900 dark:text-orange-200",
        left: "border-l-orange-500 bg-orange-500/[0.03]",
        badge: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
      };
    case "MEDIUM":
      return {
        card: "border-yellow-500/30 bg-yellow-500/[0.03] text-yellow-900 dark:text-yellow-200",
        left: "border-l-yellow-500 bg-yellow-500/[0.03]",
        badge: "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400",
      };
    case "LOW":
      return {
        card: "border-blue-500/30 bg-blue-500/[0.03] text-blue-900 dark:text-blue-200",
        left: "border-l-blue-500 bg-blue-500/[0.03]",
        badge: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
      };
    default:
      return {
        card: "border-border/30 bg-muted/[0.02] text-foreground",
        left: "border-l-muted-foreground/30 bg-muted/[0.02]",
        badge: "bg-muted/30 text-muted-foreground",
      };
  }
};

/* ──────────────────────────────────────────────────────────────
 * Component
 * ────────────────────────────────────────────────────────────── */

const SecurityScanner = ({ isOpen, onClose, backend, baseUrl, apiKey, authToken, inline = false, onSwitchTab }: SecurityScannerProps) => {
  const effectiveBaseUrl = resolveBackendUrl(baseUrl);
  const effectiveApiKey = apiKey || "";
  const [code, setCode] = useState("# Simple Code Example\ndef greet(name):\n    return f\"Hello, {name}!\"\n\nprint(greet(\"User\"))");
  const [isScanning, setIsScanning] = useState(false);
  const [runtime, setRuntime] = useState<string>("gvisor");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandedLang, setExpandedLang] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "telemetry">("dashboard");

  const detectedLang = useMemo(() => detectLanguage(code), [code]);

  // Unified job store
  const {
    jobs,
    volatileResults,
    volatileLogs,
    addJob,
    removeJob,
    openStream,
    lazyFetchResult,
    syncFromServer,
  } = useJobStore("quick-scan", effectiveBaseUrl, effectiveApiKey);

  // Keep the latest lazyFetchResult in a ref so the effect doesn't depend on its identity
  // (avoids stale closures AND refetch loops if the hook doesn't memoize it).
  const lazyFetchRef = useRef(lazyFetchResult);
  lazyFetchRef.current = lazyFetchResult;
  const requestedResultsRef = useRef<Set<string>>(new Set());

  // Auto-select latest job if any
  useEffect(() => {
    if (jobs.length > 0 && !selectedJobId) {
      setSelectedJobId(jobs[0].job_id);
    }
  }, [jobs, selectedJobId]);

  // Lazy load the report when a completed job is selected (once per job)
  useEffect(() => {
    if (!selectedJobId) return;
    const job = jobs.find((j) => j.job_id === selectedJobId);
    if (
      job &&
      isDoneStatus(job.status) &&
      !volatileResults[selectedJobId] &&
      !requestedResultsRef.current.has(selectedJobId)
    ) {
      requestedResultsRef.current.add(selectedJobId);
      Promise.resolve(lazyFetchRef.current(selectedJobId)).catch(() => {
        // allow a retry if the fetch failed
        requestedResultsRef.current.delete(selectedJobId);
      });
    }
  }, [selectedJobId, jobs, volatileResults]);

  const runScan = async () => {
    if (!code.trim()) {
      toast.error("Please provide code to scan");
      return;
    }

    if (!effectiveApiKey) {
      toast.error("No API Key configured. Use the \"API Keys\" button in the top-right to create one.", { duration: 5000 });
      return;
    }

    try {
      setIsScanning(true);

      const apiExt = detectedLang === "k8s" ? "yaml" : detectedLang === "terraform" ? "tf" : detectedLang === "rust" ? "rs" : detectedLang;
      const filename = `input.${apiExt}`;

      // POST asynchronously to support SSE streams
      const response = await fetch(`${effectiveBaseUrl}/scan-jobs?async=true`, {
        method: "POST",
        headers: {
          "accept": "application/json",
          "Content-Type": "application/json",
          "Authorization": `Bearer ${effectiveApiKey}`,
        },
        body: JSON.stringify({
          files: { [filename]: code },
          metadata: { runtime },
        }),
      });

      // Read as text first so a non-JSON body can never crash the parser
      const { data, raw, isJson } = await readBody(response);

      if (!response.ok) {
        throw new Error(errorMessageFrom(response, data, raw, "Scan request failed"));
      }
      if (!isJson || !data) {
        throw new Error(
          `Server returned a non-JSON response (HTTP ${response.status}): ${raw.slice(0, 120) || "empty body"}. ` +
          `Check that the backend URL (${effectiveBaseUrl}) is correct and that /scan-jobs is proxied.`
        );
      }

      const job_id: string | undefined = data.job_id;
      if (!job_id) throw new Error("Server response did not include a job_id");

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
          submitted_at: new Date().toISOString(),
          runtime,
        },
        summary: null,
        result: null,
        submittedAt: new Date().toISOString(),
        completedAt: null,
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
    if (!effectiveApiKey) {
      toast.error("No API key found.");
      return;
    }
    try {
      const response = await fetch(`${effectiveBaseUrl}/v1/jobs/${jobId}`, {
        method: "DELETE",
        headers: {
          "Authorization": `Bearer ${effectiveApiKey}`,
        },
      });

      // DELETE commonly returns 204 / empty body — never assume JSON
      const { data, raw } = await readBody(response);

      if (!response.ok) {
        throw new Error(errorMessageFrom(response, data, raw, "Failed to cancel job"));
      }
      toast.success("Job cancellation requested.");
      syncFromServer();
    } catch (error: any) {
      console.error("Cancel job error:", error);
      toast.error(error.message || "Failed to cancel job");
    }
  };

  // Currently selected job
  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || null;
  const isSelectedDone = isDoneStatus(selectedJob?.status);
  const isSelectedError = selectedJob?.status === "ERROR";
  const selectedResult =
    (selectedJobId && isSelectedDone ? volatileResults[selectedJobId] : null) ??
    (isSelectedDone ? selectedJob?.detail : null);

  const generateSyntheticLanguages = (filesScanned: string[], findings: any[]): Record<string, any> => {
    const languages: Record<string, any> = {};
    const files = filesScanned && filesScanned.length > 0 ? filesScanned : ["input.py"];
    const loc = code.split("\n").length || 0;

    files.forEach((filePath) => {
      const hasK8sTool = findings.some(
        (f) => (f.file || "") === filePath && K8S_TOOLS.includes((f.tool || "").toLowerCase())
      );
      const langName = languageFromPath(filePath, hasK8sTool ? "kubelinter" : "");

      if (!languages[langName]) {
        languages[langName] = {
          language: langName,
          file_count: 0,
          lines_of_code: loc,
          percentage: 0,
          findings: [],
        };
      }
      languages[langName].file_count += 1;
    });

    findings.forEach((finding) => {
      const langName = languageFromPath(finding.file || "input.py", finding.tool || "");

      if (languages[langName]) {
        languages[langName].findings.push(finding);
      } else {
        languages[langName] = {
          language: langName,
          file_count: 1,
          lines_of_code: loc,
          percentage: 0,
          findings: [finding],
        };
      }
    });

    const totalFiles = files.length || 1;
    Object.keys(languages).forEach((key) => {
      languages[key].percentage = parseFloat(((languages[key].file_count / totalFiles) * 100).toFixed(1));
    });

    return languages;
  };

  // Custom renderer for scan result findings
  const renderQuickScanResult = (result: any) => {
    const reportObj =
      result?.report && typeof result.report === "object" && (result.report.findings || result.report.summary)
        ? result.report
        : result?.detail?.report && typeof result.detail.report === "object" && (result.detail.report.findings || result.detail.report.summary)
        ? result.detail.report
        : result?.detail || result || {};

    const rawFindings: any[] =
      Array.isArray(reportObj.findings)
        ? reportObj.findings
        : Array.isArray(result?.findings)
        ? result.findings
        : Array.isArray(result?.detail?.findings)
        ? result.detail.findings
        : [];

    // Filter out internal kubernetes symlink paths and deduplicate identical findings
    const cleanFindings = rawFindings
      .filter((f: any) => {
        const fileStr = String(f?.file || "");
        return !fileStr.includes("/..") && !fileStr.startsWith("..");
      })
      .filter((f: any, idx: number, arr: any[]) =>
        idx === arr.findIndex((t: any) =>
          t.tool === f.tool && t.issue === f.issue && t.line === f.line
        )
      );

    const findings = cleanFindings.length > 0 ? cleanFindings : rawFindings;

    const rawFiles: string[] =
      (Array.isArray(reportObj.files_scanned) && reportObj.files_scanned.length > 0)
        ? reportObj.files_scanned
        : (Array.isArray(result?.files_scanned) && result.files_scanned.length > 0)
        ? result.files_scanned
        : (Array.isArray(result?.detail?.files_scanned) && result.detail.files_scanned.length > 0)
        ? result.detail.files_scanned
        : ["input." + detectedLang];

    const filesScannedFiltered = rawFiles.filter((f: string) => !f.startsWith("..") && !f.includes("/.."));
    const filesScanned = filesScannedFiltered.length > 0 ? filesScannedFiltered : ["input." + detectedLang];

    const totalFindings = findings.length;

    const hasSecurityFlaws = findings.some((f: any) => {
      const sev = (f.severity || "INFO").toUpperCase();
      return sev === "CRITICAL" || sev === "HIGH" || sev === "MEDIUM" || sev === "LOW";
    });

    const syntheticLangs = generateSyntheticLanguages(filesScanned, findings);
    const langEntries = Object.entries(syntheticLangs);

    const chartData = langEntries.map(([lang, r]) => ({
      name: lang,
      value: r && typeof r.percentage === "number" ? parseFloat(r.percentage.toFixed(1)) : 0,
    }));

    return (
      <div className="space-y-7 animate-in fade-in duration-500">
        {/* Verdict Banner */}
        <div className="p-1 flex flex-wrap items-center gap-4">
          <div className={cn(
            "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border",
            totalFindings === 0
              ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-500"
              : hasSecurityFlaws
              ? "bg-red-500/10 border-red-500/20 text-red-500"
              : "bg-amber-500/10 border-amber-500/20 text-amber-500"
          )}>
            {totalFindings === 0 ? (
              <CheckCircle2 className="w-5 h-5" />
            ) : (
              <AlertCircle className="w-5 h-5" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-black text-base text-foreground">
              {totalFindings === 0
                ? "SCAN VERDICT: SECURE"
                : hasSecurityFlaws
                ? "VULNERABILITIES DETECTED"
                : "RISKS & CODE ISSUES FOUND"}
            </p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Detected by <span className="font-semibold text-violet-500">Unified Ingestion Pipeline</span>
              {" · "}{filesScanned.length} files scanned{" · "}{code.split("\n").length} lines of code
            </p>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Badge variant="outline" className="bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/25 font-bold text-xs px-3 py-1">
              {langEntries.length} {langEntries.length === 1 ? "Language" : "Languages"}
            </Badge>
            <Badge variant="outline" className={cn(
              "font-bold text-xs px-3 py-1",
              totalFindings === 0
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/25"
                : hasSecurityFlaws
                ? "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/25"
                : "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/25"
            )}>
              {totalFindings} Findings
            </Badge>
          </div>
        </div>

        {/* Tab Toggle */}
        <div className="flex bg-muted/30 p-0.5 rounded-lg max-w-xs">
          {(["dashboard", "telemetry"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={cn(
                "flex-1 py-1 px-3 text-[10px] font-black uppercase tracking-wider rounded-md transition-all",
                activeTab === tab
                  ? "bg-violet-600 text-white shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {tab === "dashboard" ? "Dashboard" : "Telemetry"}
            </button>
          ))}
        </div>

        {activeTab === "dashboard" ? (
          <div className="space-y-7 animate-in fade-in duration-300">
            {chartData.length > 0 && (
              <div className="border-t border-border/60 pt-6 p-1 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
                    Language Distribution
                  </h3>
                  <span className="text-[10px] text-muted-foreground/60">
                    by % of codebase
                  </span>
                </div>

                {/* Stacked horizontal color bar */}
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

                {/* Legend grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 pt-1">
                  {chartData.map((d, i) => (
                    <div key={d.name} className="flex items-center justify-between text-xs py-1 border-b border-border/10">
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className="w-2 h-2 rounded-full shrink-0"
                          style={{ backgroundColor: LANG_COLORS[i % LANG_COLORS.length] }}
                        />
                        <span className="font-bold text-foreground/90 truncate">{d.name}</span>
                      </div>
                      <div className="flex items-center gap-2 text-right shrink-0">
                        <span className="font-semibold text-foreground/80">{d.value}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-Language Details */}
            <div className="border-t border-border/60 pt-6 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-muted-foreground">
                  Per-Language Details
                </h3>
                <span className="text-[10px] text-muted-foreground/60">
                  {langEntries.length} {langEntries.length === 1 ? "language" : "languages"} analyzed
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {langEntries.map(([lang, info]: [string, any], i) => {
                  const sevCounts = info.findings.reduce(
                    (acc: any, f: any) => {
                      const sev = (f.severity || "INFO").toUpperCase();
                      if (sev === "CRITICAL") acc.critical += 1;
                      else if (sev === "HIGH") acc.high += 1;
                      else if (sev === "MEDIUM") acc.medium += 1;
                      else if (sev === "LOW") acc.low += 1;
                      else acc.info += 1;
                      return acc;
                    },
                    { critical: 0, high: 0, medium: 0, low: 0, info: 0 }
                  );
                  const isSecure = info.findings.length === 0;

                  return (
                    <div key={lang} className="rounded-2xl border border-border bg-muted/10 hover:bg-muted/20 transition-all">
                      <button
                        className="w-full p-5 flex items-center justify-between text-left"
                        onClick={() => setExpandedLang(expandedLang === lang ? null : lang)}
                      >
                        <div className="flex items-center gap-3">
                          <span
                            className="w-3 h-3 rounded-full shrink-0"
                            style={{ background: LANG_COLORS[i % LANG_COLORS.length] }}
                          />
                          <span className="font-black text-sm text-foreground">{lang}</span>
                        </div>
                        <div className="flex items-center gap-3 text-right">
                          <div className="flex flex-col items-end gap-1">
                            <div className="text-[10px] font-bold text-foreground/80">
                              {info.percentage?.toFixed(1)}%
                            </div>
                            <div className="flex gap-1 items-center">
                              {isSecure ? (
                                <Badge className="h-4.5 px-1.5 text-[8px] bg-emerald-500/10 hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold rounded-md">
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
                        </div>
                      </button>

                      {expandedLang === lang && (
                        <div className="border-t border-border/50">
                          {info.findings.length === 0 ? (
                            <div className="p-5 text-xs font-semibold text-muted-foreground/60">
                              No security findings for this language
                            </div>
                          ) : (
                            <div className="overflow-y-auto p-4 space-y-2.5 max-h-[420px]">
                              {info.findings.map((f: any, fi: number) => {
                                const sev = f.severity?.toUpperCase() ?? "INFO";
                                const styles = severityStyles(sev);
                                const rawPath = f.file || "";
                                const cleanPath = rawPath.includes("/workspace/")
                                  ? rawPath.split("/workspace/")[1]
                                  : rawPath.replace(/^\.\//, "");
                                const fileName = cleanPath ? cleanPath.split("/").pop() : "";

                                return (
                                  <div key={fi} className={cn("p-3 rounded-xl border text-[11px] transition-all flex flex-col gap-1.5", styles.card)}>
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <Badge variant="outline" className={cn("text-[8px] font-black uppercase tracking-wide px-1.5 py-0 border-0 rounded-md", styles.badge)}>
                                        {sev}
                                      </Badge>
                                      <span className="text-[10px] font-bold text-muted-foreground">
                                        {f.tool} {fileName ? `(${fileName})` : ""}
                                      </span>
                                      {f.line && (
                                        <span className="text-[9px] font-mono font-bold text-muted-foreground/80 ml-auto bg-background/60 px-1.5 py-0.5 rounded border border-border/30">
                                          L:{f.line}
                                        </span>
                                      )}
                                    </div>
                                    <p className="font-semibold text-foreground/90 leading-snug">{f.issue}</p>
                                    {cleanPath && (
                                      <div className="flex items-center gap-1.5 mt-1">
                                        <p className="text-[9px] font-mono font-medium text-muted-foreground/70 bg-background/40 px-2 py-0.5 rounded border border-border/20 truncate" title={cleanPath}>
                                          📄 {cleanPath}
                                        </p>
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
        ) : (
          /* Developer split view: Report vs Insights */
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
                        const styles = severityStyles(severity);

                        return (
                          <div
                            key={i}
                            className={cn(
                              "p-4 border-l-2 border-y-0 border-r-0 rounded-r-xl text-[11px] transition-all flex flex-col gap-2 relative overflow-hidden",
                              styles.left
                            )}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className={cn("text-[8px] font-black px-1.5 py-0 border-0", styles.badge)}>
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

  /** Shared job-state rendering (in-progress / error / done / loading) for both layouts. */
  const renderJobStates = (variant: "dialog" | "inline") => {
    if (!selectedJob) return null;

    // In progress: anything that is neither done nor errored
    if (!isSelectedDone && !isSelectedError) {
      return (
        <div className="max-w-5xl mx-auto w-full">
          <UnifiedPipelineView
            job={selectedJob}
            steps={QUICK_SCAN_STEPS}
            result={selectedResult}
            onResultRender={renderQuickScanResult}
            onCancel={handleCancelJob}
            logs={selectedJobId ? volatileLogs[selectedJobId] : undefined}
          />
        </div>
      );
    }

    if (isSelectedError) {
      const isDialog = variant === "dialog";
      return (
        <div className={cn(
          "border-2 border-destructive/20 bg-destructive/5 flex flex-col",
          isDialog ? "rounded-[2rem] p-10 gap-6" : "rounded-xl p-8 gap-5"
        )}>
          <div className={cn("flex items-center text-destructive", isDialog ? "gap-4" : "gap-3")}>
            <AlertCircle className={isDialog ? "w-10 h-10" : "w-8 h-8"} />
            <h2 className={cn("font-black tracking-tight", isDialog ? "text-2xl" : "text-xl")}>Scan Failed</h2>
          </div>
          <p className={cn(
            "font-mono text-sm text-destructive/80 bg-black/5 border border-destructive/10 leading-relaxed",
            isDialog ? "rounded-2xl p-6" : "rounded-xl p-5"
          )}>
            {selectedJob.stepMessage || "An unexpected error occurred during sandbox execution."}
          </p>
        </div>
      );
    }

    // Done
    if (selectedResult) {
      return (
        <div className="max-w-5xl mx-auto w-full">
          {renderQuickScanResult(selectedResult)}
        </div>
      );
    }

    // Done but result still loading
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[300px] gap-3 text-muted-foreground/50">
        <Loader2 className="w-6 h-6 animate-spin" />
        <span className="text-xs font-semibold">Loading scan results…</span>
      </div>
    );
  };

  const renderRuntimeSelector = (microVmLabel: string) => (
    <div className="flex bg-muted/40 rounded-xl p-0.5 border border-border/50">
      <button
        type="button"
        disabled={isScanning}
        onClick={() => setRuntime("gvisor")}
        className={cn(
          "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
          runtime === "gvisor"
            ? "bg-violet-600 text-white shadow-sm"
            : "text-muted-foreground hover:text-foreground"
        )}
      >
        <Shield className="w-3 h-3" />
        gVisor
      </button>
      <button
        type="button"
        disabled={isScanning}
        onClick={() => setRuntime("kata-fc")}
        className={cn(
          "flex-grow py-1.5 text-[9px] font-black uppercase rounded-lg transition-all flex items-center justify-center gap-1",
          runtime === "kata-fc"
            ? "bg-violet-600 text-white shadow-sm"
            : "text-muted-foreground hover:text-foreground"
        )}
      >
        <Activity className="w-3 h-3" />
        {microVmLabel}
      </button>
    </div>
  );

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
                        detectedLang === tmpl.lang && code === tmpl.code ? "border-violet-500/30 bg-violet-500/5 text-violet-500" : ""
                      )}
                    >
                      <img src={tmpl.icon} alt="" className="w-3.5 h-3.5 object-contain flex-shrink-0" />
                      {tmpl.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Source Ingestion / IDE window */}
              <div className="flex flex-col gap-1.5 mt-2">
                <div className="flex items-center justify-between text-muted-foreground">
                  <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground block">
                    Source Ingestion
                  </label>
                </div>

                <div className="relative h-[380px] rounded-2xl bg-[#0b0e14] border border-border/60 overflow-hidden flex flex-col transition-all shadow-lg shadow-black/10">
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
                        {getFilename(detectedLang)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-[9px] font-black tracking-wider uppercase text-zinc-500 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">
                        {detectedLang.toUpperCase()}
                      </span>
                    </div>
                  </div>

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

              {/* Isolation Runtime Selector */}
              <div className="flex flex-col gap-2 mt-2">
                <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                  Isolation Runtime
                </label>
                {renderRuntimeSelector("MicroVM")}
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
                      const stepIdx = QUICK_SCAN_STEPS.findIndex((s) => s.key === selectedJob.status);
                      const isError = selectedJob.status === "ERROR";
                      const isDone = isSelectedDone ? true : i < stepIdx;
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
              <div className="absolute -top-24 -right-24 w-48 h-48 rounded-full bg-violet-500/10 blur-3xl" />

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

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 my-8">
                <div className="p-5 rounded-2xl border border-border/40 bg-background/50 flex flex-col gap-3">
                  <div className={cn("p-2 rounded-lg w-fit border border-emerald-500/20", COLOR_CLASSES.emerald)}>
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
                  <div className={cn("p-2 rounded-lg w-fit border border-indigo-500/20", COLOR_CLASSES.indigo)}>
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
                  <div className={cn("p-2 rounded-lg w-fit border border-amber-500/20", COLOR_CLASSES.amber)}>
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

          {renderJobStates("dialog")}
        </div>

      </div>
    </div>
  );

  if (inline) {
    const readyCards = [
      { icon: <Activity className="w-3.5 h-3.5" />, color: "emerald", title: "Isolated Sandboxes" },
      { icon: <FileCode className="w-3.5 h-3.5" />, color: "indigo", title: "Multi-Language" },
      { icon: <Zap className="w-3.5 h-3.5" />, color: "amber", title: "Deep Inspections" },
    ];

    return (
      <div className="w-full h-[calc(100vh-210px)] min-h-[600px] border-t border-b border-border flex flex-row overflow-hidden p-0 animate-in fade-in duration-500">

        {/* ── Column 1: Code Editor (24%) ── */}
        <div className="w-full md:w-[24%] shrink-0 border-r border-border flex flex-col h-full overflow-hidden">
          <div className="p-4 space-y-3 h-full flex flex-col justify-between">
            {/* Integrated Templates & Editor Box */}
            <div className="flex-grow flex flex-col min-h-0 border border-border rounded-lg bg-white dark:bg-[#0b0e14] overflow-hidden transition-all">
              {/* Quick Templates */}
              <div className="p-3 bg-gray-50/50 dark:bg-[#0f131a] border-b border-border shrink-0">
                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75 block mb-2">
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
                        detectedLang === tmpl.lang && code === tmpl.code
                          ? "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-400"
                          : ""
                      )}
                    >
                      <img src={tmpl.icon} alt="" className="w-3.5 h-3.5 object-contain flex-shrink-0" />
                      {tmpl.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Editor (Source Ingestion) */}
              <div className="flex-grow flex flex-col min-h-0">
                <div className="flex items-center justify-between px-3 py-2 bg-gray-100 dark:bg-[#111622] border-b border-border select-none shrink-0">
                  <div className="flex items-center gap-2">
                    <div className="flex gap-1">
                      <span className="w-2 h-2 rounded-full bg-[#ff5f56]" />
                      <span className="w-2 h-2 rounded-full bg-[#ffbd2e]" />
                      <span className="w-2 h-2 rounded-full bg-[#27c93f]" />
                    </div>
                    <span className="font-mono text-[10px] text-muted-foreground/70 flex items-center gap-1">
                      <FileCode className="w-3 h-3 text-violet-400" />
                      {getFilename(detectedLang)}
                    </span>
                  </div>
                  <span className="font-mono text-[8px] font-black uppercase text-muted-foreground/50 bg-muted/40 px-1 py-0.5 rounded border border-border/40">
                    {detectedLang.toUpperCase()}
                  </span>
                </div>

                {/* Code textarea (removed undefined setIsEditorFocused handlers) */}
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

            {/* Isolation Runtime Selector */}
            <div className="flex flex-col gap-2 shrink-0">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/75">
                Isolation Runtime
              </label>
              {renderRuntimeSelector("Kata-FC")}
            </div>

            <Button
              onClick={runScan}
              disabled={isScanning || !code.trim()}
              className="h-9 w-full rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-bold text-[11px] flex items-center justify-center gap-1.5 shadow-md shadow-violet-600/10 transition-all active:scale-[0.98] shrink-0 uppercase tracking-wider"
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
        <div className="w-full md:w-[20%] shrink-0 border-r border-border flex flex-col h-full overflow-hidden">
          <div className="px-3 py-2.5 border-b border-border shrink-0 flex items-center justify-between">
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
        <div className="flex-grow flex flex-col h-full overflow-y-auto p-4 bg-background/5">
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
                {readyCards.map((card) => (
                  <div key={card.title} className="flex flex-col items-center gap-2 p-3 rounded-lg border border-border/30 bg-muted/10">
                    <div className={cn("p-1.5 rounded-md", COLOR_CLASSES[card.color])}>{card.icon}</div>
                    <span className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">{card.title}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {renderJobStates("inline")}
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
