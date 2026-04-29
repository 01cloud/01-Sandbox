import { motion } from "framer-motion";
import { Layers, Search, Brain, Globe, Settings } from "lucide-react";

const features = [
  {
    icon: Layers,
    title: "Swappable Backend Architecture",
    description:
      "Seamless switching between multiple backends without a server restart — and without modifying a single line of client code.",
  },
  {
    icon: Search,
    title: "Unified Scan API",
    description: "A dedicated endpoint designed for bulk security audits and high-concurrency workflows.",
  },
  {
    icon: Brain,
    title: "Intelligent Auto-Detection",
    description: "Automatically identifies the programming language and applies the most relevant security scanner.",
  },
  {
    icon: Globe,
    title: "Transparent Proxying",
    description:
      "Securely interacts with internal backend APIs through the main gateway, hiding complex infrastructure from the public internet.",
  },
  {
    icon: Settings,
    title: "Kubernetes Native",
    description: "Native support for Horizontal Pod Autoscaling (HPA) is included out of the box.",
  },
];

const FeaturesSection = () => {
  return (
    <section className="py-28 relative bg-[hsl(var(--surface))]">
      <div className="container mx-auto px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16 max-w-3xl mx-auto"
        >
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/5 border border-accent/10 text-accent text-[10px] font-black uppercase tracking-[0.2em] mb-4">
            Explore Features
          </span>
          <h2 className="text-4xl md:text-6xl font-display font-extrabold tracking-tight">
            Your Smart <span className="text-gradient">Sandbox Platform</span>
          </h2>
        </motion.div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5 max-w-6xl mx-auto">
          {features.map((feature, i) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.08 }}
              className="p-7 rounded-3xl bg-background border border-border hover:border-accent/40 transition-all duration-300 group"
            >
              <div className="w-11 h-11 rounded-2xl bg-secondary flex items-center justify-center mb-5 group-hover:scale-105 transition-transform">
                <feature.icon className="w-5 h-5 text-secondary-foreground" />
              </div>
              <h3 className="font-display font-bold text-lg mb-2 tracking-tight">{feature.title}</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">{feature.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default FeaturesSection;
