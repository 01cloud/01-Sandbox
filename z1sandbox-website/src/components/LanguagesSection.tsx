import { motion } from "framer-motion";

const languages = [
  { name: "Python", icon: "/logos/python.svg" },
  { name: "Node.js", icon: "/logos/nodejs.svg" },
  { name: "Go", icon: "/logos/go-icon.svg" },
  { name: "Rust", icon: "/logos/rust.svg" },
  { name: "C++", icon: "/logos/cplusplus.svg" },
  { name: "Java", icon: "/logos/java.svg" },
  { name: "Ruby", icon: "/logos/ruby.svg" },
  { name: "PHP", icon: "/logos/php.svg" },
  { name: "Swift", icon: "/logos/swift.svg" },
  { name: "TypeScript", icon: "/logos/typescript.svg" },
  { name: "Bash", icon: "/logos/bash.svg" },
  { name: "YAML", icon: "/logos/yaml.svg" },
  { name: "SQL", icon: "/logos/azuresql.svg" },
  { name: "Dockerfile", icon: "/logos/docker.svg" },
  { name: "Kubernetes", icon: "/logos/kubernetes.svg" },
];

const LanguagesSection = () => {
  return (
    <section className="py-20 border-y border-border/50 overflow-hidden">
      <div className="container mx-auto px-6">
        <div className="flex flex-col md:flex-row items-center justify-between gap-12">
          <div className="md:w-1/3">
            <h3 className="text-2xl font-display font-extrabold tracking-tight mb-4 text-center md:text-left">
              Multi-Language <span className="text-gradient">Ready</span>
            </h3>
            <p className="text-muted-foreground text-sm text-center md:text-left leading-relaxed">
              Native support for top programming environments and infrastructure tools. 01 Sandbox automatically detects the runtime and provisions the correct environment in milliseconds.
            </p>
          </div>
          <div className="md:w-2/3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 sm:gap-4">
            {languages.map((lang, i) => (
              <motion.div
                key={lang.name}
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.03 }}
                className="flex items-center gap-3 px-4 py-3 rounded-xl bg-card/40 backdrop-blur-sm border border-border/50 hover:border-accent/40 hover:bg-card/60 shadow-sm hover:shadow-accent/5 transition-all group cursor-default"
              >
                <img
                  src={lang.icon}
                  alt={`${lang.name} logo`}
                  className="w-5 h-5 object-contain group-hover:scale-110 transition-transform duration-300 flex-shrink-0"
                />
                <span className="text-xs font-bold tracking-tight text-foreground/70 group-hover:text-foreground transition-colors">{lang.name}</span>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default LanguagesSection;
