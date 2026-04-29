import { motion } from "framer-motion";
import { Zap, Shield, Wand2, Server } from "lucide-react";

const reasons = [
  {
    icon: Zap,
    title: "Fast response times",
    description:
      "Jobs get picked up almost instantly (usually under 100ms), even when multiple users are hitting the system at once. No noticeable lag during normal usage.",
  },
  {
    icon: Shield,
    title: "Security that actually holds up",
    description:
      "Each run is isolated with gVisor, storage isn't shared between workloads, and scans run automatically in the background. Everything stays contained the way you'd expect.",
  },
  {
    icon: Wand2,
    title: "Simple to operate",
    description:
      "No manual setup needed — languages are detected automatically, and security tools run in the background without extra configuration.",
  },
  {
    icon: Server,
    title: "Ready for production use",
    description:
      "Handles 50+ concurrent users without slowing down, and scales normally with Kubernetes when demand increases.",
  },
];

const WhySection = () => {
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
            Why 01 Sandbox
          </span>
          <h2 className="text-4xl md:text-6xl font-display font-extrabold tracking-tight">
            More Than Just a <span className="text-gradient">Sandbox.</span>
          </h2>
        </motion.div>

        <div className="grid md:grid-cols-2 gap-5 max-w-5xl mx-auto">
          {reasons.map((reason, i) => (
            <motion.div
              key={reason.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.08 }}
              className="flex gap-5 p-7 rounded-3xl border border-border bg-card hover:shadow-[0_8px_40px_-12px_hsl(265_85%_60%/0.15)] transition-all"
            >
              <div className="w-12 h-12 rounded-2xl pill-badge flex items-center justify-center shrink-0">
                <reason.icon className="w-5 h-5 text-foreground" />
              </div>
              <div>
                <h3 className="font-display font-bold text-lg mb-2 tracking-tight">{reason.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{reason.description}</p>
              </div>
            </motion.div>
          ))}
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-20 max-w-4xl mx-auto p-10 md:p-14 rounded-[2rem] bg-foreground text-background text-center relative overflow-hidden"
        >
          <div className="absolute inset-0 opacity-30 gradient-orb" />
          <div className="relative">
            <h3 className="text-3xl md:text-5xl font-display font-extrabold tracking-tight mb-4">
              Ready To Ship Safer Code?
            </h3>
            <p className="text-background/70 max-w-xl mx-auto mb-8">
              Plug 01 Sandbox into your AI agents, CI/CD pipelines, and multi-tenant cloud
              applications in minutes.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 justify-center">

              <a
                href="/book-a-demo"
                className="px-6 py-3 rounded-full border border-background/20 text-background font-medium text-sm hover:bg-background/10 transition-colors"
              >
                Book a Demo
              </a>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
};

export default WhySection;
