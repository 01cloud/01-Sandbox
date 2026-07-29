import { describe, it, expect } from "vitest";

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

  // Terraform / HCL: Must be top-level blocks or HCL definitions
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

  // JS & TS Shared Patterns
  if (/\b(const|let|var)\s+\w+\s*=/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\brequire\s*\(\s*['"]/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\bimport\s+.*from\s+['"]/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\bexport\s+(default|const|let|function|class|\{)/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bfunction\s+\w+\s*\(/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bconsole\.(log|error|warn|info)\(/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/\bapp\.(get|post|put|delete|listen|use)\(/.test(text)) { scores.js += 15; scores.ts += 15; }
  if (/\b(req|res|next)\b/.test(text)) { scores.js += 10; scores.ts += 10; }
  if (/=>\s*\{?/.test(text)) { scores.js += 5; scores.ts += 5; }

  // TypeScript Specific Patterns
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

describe("SecurityScanner Language Detection & Filename Assignment", () => {
  it("should correctly identify JavaScript template snippet as JS and map to index.js", () => {
    const jsCode = `// JavaScript XSS & Prototype Pollution Example
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
`;

    const lang = detectLanguage(jsCode);
    expect(lang).toBe("js");
    expect(getFilename(lang)).toBe("index.js");
  });

  it("should correctly identify TypeScript template snippet as TS and map to index.ts", () => {
    const tsCode = `// TypeScript unsafe any & eval example
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
`;

    const lang = detectLanguage(tsCode);
    expect(lang).toBe("ts");
    expect(getFilename(lang)).toBe("index.ts");
  });

  it("should correctly identify Rust template snippet as RS and map to main.rs", () => {
    const rsCode = `// Rust unsafe memory & command injection example
use std::process::Command;

fn run_command(user_input: &str) {
    let output = Command::new("sh")
        .arg("-c")
        .arg(user_input)
        .output()
        .expect("Failed to execute");
    println!("{:?}", output);
}

unsafe fn read_arbitrary_memory(ptr: *const u32) -> u32 {
    *ptr
}

fn main() {
    run_command("whoami; cat /etc/passwd");
}
`;

    const lang = detectLanguage(rsCode);
    expect(lang).toBe("rs");
    expect(getFilename(lang)).toBe("main.rs");
  });

  it("should correctly identify Terraform template snippet as terraform and map to main.tf", () => {
    const tfCode = `# Terraform IaC Security Issues Example
resource "aws_s3_bucket" "data" {
  bucket = "company-data-bucket"
}

resource "aws_s3_bucket_acl" "data" {
  bucket = aws_s3_bucket.data.id
  acl    = "public-read"
}
`;

    const lang = detectLanguage(tfCode);
    expect(lang).toBe("terraform");
    expect(getFilename(lang)).toBe("main.tf");
  });
});
