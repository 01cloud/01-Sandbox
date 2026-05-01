import { motion } from "framer-motion";
import { ShieldCheck, Rocket, Database } from "lucide-react";

const pillars = [
  {
    icon: ShieldCheck,
    title: "Kernel-Level Isolation",
    description:
      "Powered by a secure, isolated runtime,  Sandbox provides a strong layer of defense. Vulnerabilities are trapped within the sandbox kernel, protecting host infrastructure from malicious actors.",
  },
  {
    icon: Rocket,
    title: "Fire-and-Forget",
    description:
      "Rapid bursts of API requests keep the application responsive for 50+ concurrent users — returning a security inspection report instantly for a seamless experience.",
  },
  {
    icon: Database,
    title: "Isolated Persistence",
    description:
      "Every execution job is assigned a unique, cryptographically secure path on shared storage. Source code from one job never leaks into another.",
  },
];

const PillarsSection = () => {
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
            Explore Pillars
          </span>
          <h2 className="text-4xl md:text-6xl font-display font-extrabold tracking-tight">
            Built on Three <span className="text-gradient">Key Pillars</span>
          </h2>
          <p className="text-muted-foreground mt-5 text-lg">
            A platform designed to execute untrusted code in isolated environments with strict security compliance.
          </p>
        </motion.div>

        <div className="grid md:grid-cols-3 gap-5 max-w-6xl mx-auto">
          {pillars.map((pillar, i) => (
            <motion.div
              key={pillar.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
              className="p-8 rounded-3xl border border-border bg-card hover:shadow-[0_8px_40px_-12px_hsl(265_85%_60%/0.18)] hover:-translate-y-1 transition-all duration-300"
            >
              <div className="w-12 h-12 rounded-2xl pill-badge flex items-center justify-center mb-6">
                <pillar.icon className="w-6 h-6 text-foreground" strokeWidth={2} />
              </div>
              <h3 className="text-xl font-display font-bold mb-3 tracking-tight">{pillar.title}</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">{pillar.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default PillarsSection;
