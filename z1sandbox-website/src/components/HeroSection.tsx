import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

const HeroSection = () => {
  return (
    <section className="relative min-h-screen flex items-center justify-center overflow-hidden pt-32 pb-20">
      {/* Background gradient orb */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/4 md:top-1/3 left-1/2 -translate-x-1/2 w-[350px] sm:w-[600px] md:w-[1100px] h-[300px] sm:h-[500px] md:h-[700px] gradient-orb opacity-60 md:opacity-100" />
      </div>

      <div className="relative z-10 container mx-auto px-6 text-center">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
        >
          <a
            href="/changelog"
            className="inline-flex items-center gap-2.5 px-4 py-2 rounded-full pill-badge mb-8 sm:mb-10 hover:border-primary/40 hover:bg-foreground/[0.04] transition-all hover:scale-[1.02] group cursor-pointer shadow-sm"
          >
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[11px] sm:text-xs font-black uppercase tracking-wider">
              <span className="flex h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              v0.7.12
            </span>
            <span className="text-xs sm:text-sm font-bold text-foreground/85 tracking-tight group-hover:text-foreground transition-colors">
              Kata Firecracker & Multi-Cluster OCM is live
            </span>
            <ArrowRight className="w-3.5 h-3.5 text-muted-foreground/70 group-hover:text-foreground group-hover:translate-x-0.5 transition-all" />
          </a>

          <h1 className="font-display font-black text-4xl sm:text-6xl md:text-7xl lg:text-8xl leading-[1.05] tracking-tight max-w-5xl mx-auto">
            Execute Untrusted Code In
            <br />
            <span className="text-gradient">Isolated Environments.</span>
          </h1>

          <p className="text-base sm:text-xl text-muted-foreground/90 max-w-2xl mx-auto mt-6 sm:mt-8 leading-relaxed">
            01 Sandbox runs every line in a hardened pod, auto-audits for
            vulnerabilities, secrets, and policy violations — without touching your host kernel.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.3 }}
          className="mt-16 sm:mt-24 grid grid-cols-1 sm:grid-cols-3 gap-8 sm:gap-6 max-w-3xl mx-auto border-t border-border/50 pt-12"
        >
          {[
            { label: "Job pickup", value: "<100ms" },
            { label: "Concurrent users", value: "50+" },
            { label: "Defense layers", value: "Multi" },
          ].map((s) => (
            <div key={s.label} className="text-center group">
              <div className="text-4xl sm:text-5xl font-display font-black text-foreground group-hover:text-accent transition-colors">{s.value}</div>
              <div className="text-xs sm:text-sm font-bold uppercase tracking-widest text-muted-foreground mt-2">{s.label}</div>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
};

export default HeroSection;
