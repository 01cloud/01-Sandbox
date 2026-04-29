import { motion } from "framer-motion";

const tools = [
  { name: "Semgrep", capability: "Logic Auditing", purpose: "Catches SQL Injection, Command Injection, and Path Traversal." },
  { name: "Gitleaks", capability: "Secret Discovery", purpose: "Detects hardcoded API keys, tokens, and credentials." },
  { name: "Bandit", capability: "Python Security", purpose: "Specialized linter for Python security anti-patterns." },
  { name: "Trivy", capability: "Filesystem Safety", purpose: "Scans for OS vulnerabilities and misconfigured package manifests." },
  { name: "YAMLlint", capability: "Configuration", purpose: "Ensures YAML files follow valid syntax and formatting standards." },
  { name: "Kube-linter", capability: "Best Practices", purpose: "Enforces production-readiness by checking manifests for security misconfigurations." },
  { name: "Kubeconform", capability: "Schema Validation", purpose: "Validates manifests against official Kubernetes JSON schemas for compatibility." },
];

const SecurityPipeline = () => {
  return (
    <section className="py-28 relative">
      <div className="container mx-auto px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16 max-w-3xl mx-auto"
        >
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/5 border border-accent/10 text-accent text-[10px] font-black uppercase tracking-[0.2em] mb-4">
            Security Pipeline
          </span>
          <h2 className="text-4xl md:text-6xl font-display font-extrabold tracking-tight">
            Automated <span className="text-gradient">Security Toolchain</span>
          </h2>
          <p className="text-muted-foreground mt-5 text-lg">
            Every sandbox comes pre-equipped with a strict toolchain that audits code in real time.
          </p>
        </motion.div>

        <div className="max-w-5xl mx-auto rounded-3xl border border-border bg-card overflow-hidden shadow-2xl">
          <div className="hidden md:grid grid-cols-[1fr_1.2fr_2.5fr] gap-4 px-8 py-5 bg-secondary/80 backdrop-blur-sm border-b border-border sticky top-0 z-10">
            <span className="text-sm font-extrabold text-foreground uppercase tracking-widest">Tool</span>
            <span className="text-sm font-extrabold text-foreground uppercase tracking-widest">Capability</span>
            <span className="text-sm font-extrabold text-foreground uppercase tracking-widest">Purpose</span>
          </div>

          <div className="max-h-[600px] md:max-h-[480px] overflow-y-auto custom-scrollbar">
            {tools.map((tool, i) => (
              <motion.div
                key={tool.name}
                initial={{ opacity: 0, x: -10 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.06 }}
                className="grid grid-cols-1 md:grid-cols-[1fr_1.2fr_2.5fr] gap-3 md:gap-4 px-6 md:px-8 py-6 md:py-5 border-b border-border last:border-b-0 hover:bg-[hsl(var(--surface-hover))] transition-colors group"
              >
                <div className="flex items-center justify-between md:block">
                  <span className="md:hidden text-[10px] font-bold text-muted-foreground uppercase tracking-[0.2em]">Tool</span>
                  <div className="font-display font-black text-lg md:text-base group-hover:text-foreground transition-colors">{tool.name}</div>
                </div>
                <div className="flex items-center justify-between md:block">
                  <span className="md:hidden text-[10px] font-bold text-muted-foreground uppercase tracking-[0.2em] mb-1">Capability</span>
                  <div className="flex items-center">
                    <span className="px-3 py-1 rounded-full pill-badge text-[10px] font-black text-foreground ring-1 ring-white/10 dark:ring-black/10">
                      {tool.capability}
                    </span>
                  </div>
                </div>
                <div className="flex flex-col md:block">
                  <span className="md:hidden text-[10px] font-bold text-muted-foreground uppercase tracking-[0.2em] mb-1">Purpose</span>
                  <div className="text-sm text-muted-foreground group-hover:text-foreground/90 transition-colors leading-relaxed font-medium">
                    {tool.purpose}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.4 }}
          className="mt-16 flex flex-wrap items-center justify-center gap-10 opacity-80"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-foreground/10 border border-foreground/20 flex items-center justify-center font-bold text-xs">ISO</div>
            <span className="text-sm font-bold tracking-widest uppercase">Built to Support ISO 27001</span>
          </div>
          <div className="w-px h-6 bg-border hidden md:block" />
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-foreground/10 border border-foreground/20 flex items-center justify-center font-bold text-xs">SOC2</div>
            <span className="text-sm font-bold tracking-widest uppercase">SOC 2 Type II Ready Controls</span>
          </div>
        </motion.div>
        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.6 }}
          className="mt-8 text-center text-sm text-muted-foreground/70 max-w-xl mx-auto leading-relaxed font-medium"
        >
          Architected to ensure security, auditability, and operational controls meet modern industry expectations.
        </motion.p>
      </div>
    </section>
  );
};

export default SecurityPipeline;
