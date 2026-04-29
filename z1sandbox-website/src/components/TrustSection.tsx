import { motion } from "framer-motion";

const TrustSection = () => {
  return (
    <section className="py-12 bg-background border-b border-border/50">
      <div className="container mx-auto px-6">
        <div className="flex justify-center mb-8">
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/5 border border-accent/10 text-accent text-[10px] font-black uppercase tracking-[0.2em]">
            Trusted by teams at
          </span>
        </div>
        <div className="group flex justify-center items-center gap-4 transition-all duration-500">
          <img
            src="/01cloud.png"
            alt="01Cloud Logo"
            className="h-10 w-auto transition-all duration-500"
          />
          <span className="text-2xl font-display font-black tracking-tighter text-muted-foreground group-hover:text-foreground transition-colors duration-500">
            Cloud
          </span>
        </div>
      </div>
    </section>
  );
};

export default TrustSection;
