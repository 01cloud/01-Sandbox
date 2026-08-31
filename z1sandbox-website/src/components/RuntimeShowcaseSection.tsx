import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Shield, Cpu, Zap, Terminal, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface RuntimeSpec {
  id: string;
  name: string;
  badge: string;
  tagline: string;
  icon: typeof Shield;
  isolationLevel: string;
  coldStart: string;
  securityBoundary: string;
  virtualizationTech: string;
  memoryOverhead: string;
  description: string;
  keyFeatures: string[];
  codeScanningRole: string;
  architectureNotes: string;
}

const runtimes: RuntimeSpec[] = [
  {
    id: "gvisor",
    name: "gVisor Sandbox",
    badge: "Rule-Based Kernel Isolation",
    tagline: "Google User-Space Kernel Sandboxing for High-Density Code Scanning",
    icon: Shield,
    isolationLevel: "Kernel Syscall Interception (Sentry)",
    coldStart: "~50ms ultra-fast cold start",
    securityBoundary: "User-space syscall trap (seccomp-bpf)",
    virtualizationTech: "Go-based Sentry Kernel + Gofer File Proxy",
    memoryOverhead: "Minimal (~15MB per sandbox)",
    description:
      "gVisor is an open-source, user-space kernel developed by Google. It implements a substantial portion of the Linux system call interface, acting as a security proxy between untrusted scanned code and the host operating system kernel.",
    keyFeatures: [
      "Intercepts system calls in user space using seccomp-bpf sandbox rules",
      "Prevents untrusted code execution from accessing host kernel primitives",
      "Ideal for rapid static analysis, AST parsing, and high-concurrency repo scanning",
      "Includes isolated memory management and Gofer virtual file system proxies"
    ],
    codeScanningRole:
      "Provisions instant isolated execution pods for static analysis tools (Semgrep, Gitleaks, Bandit, Trivy) with zero startup latency.",
    architectureNotes: "Untrusted Code -> gVisor Sentry -> Gofer VFS -> Host Kernel (Blocked)"
  },
  {
    id: "kata-containers",
    name: "Kata Containers",
    badge: "Hardware-Assisted MicroVM",
    tagline: "Hardware-Isolated MicroVM Sandboxing Powered by Firecracker / QEMU",
    icon: Cpu,
    isolationLevel: "Hardware VT-x / AMD-V MicroVM Hypervisor",
    coldStart: "~150ms microVM initialization",
    securityBoundary: "Hardware Hypervisor Boundary (Guest Kernel)",
    virtualizationTech: "Firecracker VMM / QEMU-lite + KVM",
    memoryOverhead: "Lightweight MicroVM (~128MB)",
    description:
      "Kata Containers delivers lightweight virtual machines that feel and perform like containers while providing real workload isolation. Scanned code runs inside a dedicated guest Linux kernel protected by hardware virtualization instructions.",
    keyFeatures: [
      "Hardware-level CPU virtualization (KVM) separating host from untrusted workloads",
      "Dedicated guest kernel per scanning execution job to guarantee total tenant isolation",
      "Prevents privilege escalation and container breakout attacks at hardware level",
      "Native integration with Kubernetes CRI for isolated microVM container pods"
    ],
    codeScanningRole:
      "Provides hard hypervisor boundaries for executing untrusted dependencies, multi-language binary analysis, and untrusted container image inspection.",
    architectureNotes: "Untrusted Code -> Dedicated Guest Kernel -> Firecracker MicroVM -> Host KVM"
  }
];

export const RuntimeShowcaseSection = () => {
  const [selectedRuntime, setSelectedRuntime] = useState<string>("gvisor");

  const active = runtimes.find((r) => r.id === selectedRuntime) || runtimes[0];

  return (
    <section id="runtimes" className="py-24 relative bg-[hsl(var(--surface))]">
      <div className="container mx-auto px-6">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-12 max-w-3xl mx-auto"
        >
          <span className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-accent/10 border border-accent/20 text-accent text-[10px] font-black uppercase tracking-[0.2em] mb-4">
            Isolated Runtime Environments
          </span>
          <h2 className="text-3xl md:text-5xl font-display font-extrabold tracking-tight">
            Hardened Sandboxes For <span className="text-gradient">Secured Code Scanning</span>
          </h2>
          <p className="text-muted-foreground mt-4 text-base md:text-lg leading-relaxed font-medium">
            01 Sandbox provisions gVisor kernel-isolated pods and Kata Container microVMs on demand to execute untrusted repository analysis without exposing host infrastructure.
          </p>
        </motion.div>

        {/* Toggle Selector Buttons */}
        <div className="max-w-4xl mx-auto mb-10 flex flex-col sm:flex-row gap-3 p-1.5 rounded-2xl bg-card border border-border/80 shadow-md">
          {runtimes.map((runtime) => {
            const Icon = runtime.icon;
            const isSelected = selectedRuntime === runtime.id;
            return (
              <button
                key={runtime.id}
                type="button"
                onClick={() => setSelectedRuntime(runtime.id)}
                className={cn(
                  "flex-1 py-4 px-6 rounded-xl font-display font-bold text-sm sm:text-base transition-all flex items-center justify-center gap-3 border text-left cursor-pointer",
                  isSelected
                    ? "bg-foreground text-background shadow-lg border-transparent scale-[1.01]"
                    : "bg-transparent text-muted-foreground hover:text-foreground border-transparent hover:bg-muted/40"
                )}
              >
                <div
                  className={cn(
                    "w-9 h-9 rounded-lg flex items-center justify-center shrink-0 transition-colors",
                    isSelected ? "bg-background/20 text-background" : "bg-muted text-foreground"
                  )}
                >
                  <Icon className="w-5 h-5" />
                </div>
                <div>
                  <div className="leading-snug">{runtime.name}</div>
                  <div className={cn("text-[10px] font-mono font-medium opacity-80", isSelected ? "text-background/80" : "text-muted-foreground")}>
                    {runtime.badge}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Selected Runtime Description & Full Specifications Card */}
        <AnimatePresence mode="wait">
          <motion.div
            key={active.id}
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -15 }}
            transition={{ duration: 0.25 }}
            className="max-w-5xl mx-auto rounded-3xl border border-border bg-card shadow-xl overflow-hidden"
          >
            {/* Header Banner */}
            <div className="p-8 md:p-10 border-b border-border/70 bg-gradient-to-r from-accent/5 via-transparent to-accent/5">
              <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
                <span className="px-3 py-1 rounded-full text-xs font-black uppercase tracking-wider bg-violet-600/10 text-violet-600 border border-violet-600/20 dark:bg-violet-400/10 dark:text-violet-400 dark:border-violet-400/20">
                  {active.badge}
                </span>
                <span className="text-xs font-mono font-semibold text-muted-foreground flex items-center gap-1.5">
                  <Zap className="w-3.5 h-3.5 text-amber-500" />
                  {active.coldStart}
                </span>
              </div>
              <h3 className="text-2xl md:text-4xl font-display font-extrabold tracking-tight text-foreground mb-3">
                {active.name}
              </h3>
              <p className="text-muted-foreground text-base md:text-lg leading-relaxed font-medium">
                {active.tagline}
              </p>
            </div>

            {/* Body Content */}
            <div className="p-8 md:p-10 grid grid-cols-1 lg:grid-cols-12 gap-8">
              {/* Left Column: Description & Capabilities (7 cols) */}
              <div className="lg:col-span-7 space-y-6">
                <div>
                  <h4 className="text-xs font-black uppercase tracking-[0.2em] text-muted-foreground mb-2">
                    Overview & Security Architecture
                  </h4>
                  <p className="text-sm text-foreground/90 leading-relaxed font-medium">
                    {active.description}
                  </p>
                </div>

                <div>
                  <h4 className="text-xs font-black uppercase tracking-[0.2em] text-muted-foreground mb-3">
                    Key Capabilities
                  </h4>
                  <ul className="space-y-2.5">
                    {active.keyFeatures.map((feat, idx) => (
                      <li key={idx} className="flex items-start gap-2.5 text-xs md:text-sm text-foreground/80 font-medium">
                        <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                        <span>{feat}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="p-4 rounded-2xl bg-muted/40 border border-border/60">
                  <h4 className="text-xs font-black uppercase tracking-[0.15em] text-accent mb-1 flex items-center gap-1.5">
                    <Terminal className="w-3.5 h-3.5" /> Scanned Code Provisioning
                  </h4>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {active.codeScanningRole}
                  </p>
                </div>
              </div>

              {/* Right Column: Technical Specification Matrix (5 cols) */}
              <div className="lg:col-span-5 flex flex-col justify-between gap-4 p-6 rounded-2xl bg-secondary/50 border border-border/80">
                <h4 className="text-xs font-black uppercase tracking-[0.2em] text-foreground border-b border-border/60 pb-3">
                  Technical Specifications
                </h4>

                <div className="space-y-4 text-xs">
                  <div>
                    <span className="text-muted-foreground block text-[10px] uppercase font-bold tracking-widest">Isolation Layer</span>
                    <span className="font-semibold text-foreground">{active.isolationLevel}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block text-[10px] uppercase font-bold tracking-widest">Security Boundary</span>
                    <span className="font-semibold text-foreground">{active.securityBoundary}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block text-[10px] uppercase font-bold tracking-widest">Virtualization Tech</span>
                    <span className="font-semibold text-foreground">{active.virtualizationTech}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block text-[10px] uppercase font-bold tracking-widest">Memory Overhead</span>
                    <span className="font-semibold text-foreground">{active.memoryOverhead}</span>
                  </div>
                </div>

                <div className="mt-2 p-3 rounded-xl bg-background border border-border font-mono text-[10px] text-muted-foreground">
                  <span className="text-accent font-bold block mb-1">DATA FLOW PIPELINE:</span>
                  {active.architectureNotes}
                </div>
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>
    </section>
  );
};

export default RuntimeShowcaseSection;
