import { motion } from "framer-motion";

const languages = [
  { name: "Python", icon: "🐍" },
  { name: "Node.js", icon: "🟢" },
  { name: "Go", icon: "🔵" },
  { name: "Rust", icon: "🦀" },
  { name: "C++", icon: "📁" },
  { name: "Java", icon: "☕" },
  { name: "Bash", icon: "🐚" },
  { name: "YAML", icon: "📜" },
];

const LanguagesSection = () => {
  return (
    <section className="py-20 border-y border-border/50 bg-secondary/30 overflow-hidden">
      <div className="container mx-auto px-6">
        <div className="flex flex-col md:flex-row items-center justify-between gap-12">
          <div className="md:w-1/3">
            <h3 className="text-2xl font-display font-extrabold tracking-tight mb-4 text-center md:text-left">
              Multi-Language <span className="text-gradient">Ready</span>
            </h3>
            <p className="text-muted-foreground text-sm text-center md:text-left leading-relaxed">
              Native support for top programming environments. 01 automatically detects the runtime and provisions the correct environment in milliseconds.
            </p>
          </div>
          <div className="md:w-2/3 flex flex-wrap justify-center md:justify-end gap-x-4 sm:gap-x-8 gap-y-6">
            {languages.map((lang, i) => (
              <motion.div
                key={lang.name}
                initial={{ opacity: 0, scale: 0.9 }}
                whileInView={{ opacity: 1, scale: 1 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.05 }}
                className="flex items-center gap-2 px-5 py-3 rounded-2xl bg-background border border-border/60 hover:border-accent/40 shadow-sm transition-all text-foreground/80 hover:text-foreground"
              >
                <span className="text-xl">{lang.icon}</span>
                <span className="text-sm font-bold tracking-tight">{lang.name}</span>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default LanguagesSection;
