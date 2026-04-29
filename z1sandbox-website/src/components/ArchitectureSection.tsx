import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

const steps = [
  {
    label: "User / Client",
    sub: "Auth0 JWT / API-Key",
    desc: "Authenticates via Auth0 or secure API keys for identity-based access."
  },
  {
    label: "Agent Gateway",
    sub: "TLS & Auth Enforcement",
    desc: "Enforces strict TLS termination and validates credentials at the edge."
  },
  {
    label: "API Server",
    sub: "FastAPI Orchestration",
    desc: "The 'brain' that validates schemas and manages cross-component job sessions."
  },
  {
    label: "01 Sandbox API",
    sub: "PVC & Lifecycle",
    desc: "Orchestrates storage on PVCs and manages the lifecycle of execution pods."
  },
  {
    label: "Scanner Pod",
    sub: "gVisor Performance",
    desc: "Executes untrusted code within gVisor-hardened, kernel-isolated containers."
  },
  {
    label: "Result Delivery",
    sub: "Sync JSON Response",
    desc: "Aggregates multi-tool findings into a structured report, delivered instantly."
  },
];

const ArchitectureSection = () => {
  return (
    <section id="architecture" className="py-28 relative bg-[hsl(var(--surface))]">
      <div className="container mx-auto px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16 max-w-3xl mx-auto"
        >
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/5 border border-accent/10 text-accent text-[10px] font-black uppercase tracking-[0.2em] mb-4">
            Technical Architecture
          </span>
          <h2 className="text-4xl md:text-6xl font-display font-extrabold tracking-tight">
            The <span className="text-gradient">Facade Pattern</span>
          </h2>
          <p className="text-muted-foreground mt-5 text-lg">
            A high-performance pipeline that manages secure traffic from Auth0-protected entries
            to gVisor-hardened execution pods with synchronous result delivery.
          </p>
        </motion.div>

        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {steps.map((step, i) => (
              <motion.div
                key={step.label}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1 }}
                className="relative group"
              >
                <div className="h-full px-6 py-6 rounded-3xl border border-border bg-card/50 backdrop-blur-sm hover:border-accent/40 hover:bg-card transition-all duration-300">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-8 h-8 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center text-accent text-xs font-bold">
                      {i + 1}
                    </div>
                    <div className="font-display font-bold text-base">{step.label}</div>
                  </div>
                  <div className="text-xs font-bold text-accent uppercase tracking-widest mb-2">{step.sub}</div>
                  <div className="text-sm text-muted-foreground leading-relaxed">
                    {step.desc}
                  </div>
                </div>
                {i < steps.length - 1 && i % 3 !== 2 && (
                  <ArrowRight className="absolute -right-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground hidden lg:block z-10" />
                )}
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ArchitectureSection;
