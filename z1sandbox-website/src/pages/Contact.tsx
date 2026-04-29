import { motion } from "framer-motion";
import { Mail, MessageSquare, Github, Globe, Linkedin } from "lucide-react";

const XIcon = ({ className }: { className?: string }) => (
  <span className={`${className} flex items-center justify-center font-display font-black text-lg pb-0.5`}>X</span>
);

const Contact = () => {
  const contactMethods = [
    { label: "Email", icon: Mail, href: "mailto:info@01security.com", desc: "info@01security.com" },
    { label: "Community", icon: MessageSquare, href: "https://slack.com", desc: "Join Slack" },
    { label: "Open Source", icon: Github, href: "https://github.com", desc: "Contribute" },
    { label: "LinkedIn", icon: Linkedin, href: "https://linkedin.com", desc: "Our Profile" },
    { label: "X", icon: XIcon, href: "https://x.com", desc: "Brand Updates" },
  ];

  return (
    <div className="min-h-screen text-foreground selection:bg-accent selection:text-white">
      <main className="pt-40 pb-32">
        <div className="container mx-auto px-6 max-w-7xl">

          {/* Minimalist Header */}
          <div className="text-center mb-24">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/5 border border-accent/10 text-accent text-[10px] font-black uppercase tracking-[0.2em] mb-8"
            >
              Get in touch
            </motion.div>
            <motion.h1
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1 }}
              className="text-6xl md:text-8xl font-display font-black tracking-tightest mb-8"
            >
              Contact Us
            </motion.h1>
            <motion.p
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
              className="text-lg text-muted-foreground/60 max-w-md mx-auto leading-relaxed"
            >
              Building the future of secure code execution. Reach out via our official channels.
            </motion.p>
          </div>

          {/* Clean Card Grid - Center items for balanced 5-col layout */}
          <div className="flex flex-wrap justify-center gap-4 mb-32">
            {contactMethods.map((method, idx) => (
              <motion.a
                key={method.label}
                href={method.href}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.3 + idx * 0.1 }}
                className="group relative p-8 md:p-10 rounded-[2rem] bg-secondary/20 border border-border/40 hover:bg-secondary/40 hover:border-accent/20 transition-all duration-500 overflow-hidden min-w-[200px] flex-1"
              >
                <div className="relative z-10">
                  <div className="w-12 h-12 rounded-2xl bg-foreground/5 flex items-center justify-center mb-6 group-hover:scale-110 group-hover:bg-accent group-hover:text-white transition-all duration-500 ring-1 ring-border/50 group-hover:ring-accent/40">
                    <method.icon className="w-5 h-5" />
                  </div>
                  <h3 className="text-xl font-bold tracking-tight mb-1">{method.label}</h3>
                  <p className="text-[10px] font-black uppercase tracking-[0.15em] text-muted-foreground/50 group-hover:text-accent/60 transition-colors">
                    {method.desc}
                  </p>
                </div>
                <div className="absolute top-0 right-0 w-32 h-32 bg-accent/5 rounded-full blur-3xl -mr-16 -mt-16 opacity-0 group-hover:opacity-100 transition-opacity duration-1000" />
              </motion.a>
            ))}
          </div>

          {/* Minimalist Office Section */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1, delay: 0.8 }}
            className="flex flex-col items-center pt-24 border-t border-border/50"
          >
            <div className="flex items-center gap-2 mb-8 text-muted-foreground/30">
              <Globe className="w-4 h-4" />
              <span className="text-[9px] font-black uppercase tracking-[0.3em]">Headquarters</span>
            </div>
            <div className="text-center group cursor-default">
              <h2 className="text-2xl font-black tracking-tight mb-4 group-hover:text-accent transition-colors">Nepal</h2>
              <p className="text-muted-foreground leading-relaxed text-sm font-medium">
                1st floor, Prasiddhi Towers<br />
                Pulchowk Rd, Lalitpur, 44600
              </p>
            </div>
          </motion.div>

        </div>
      </main>
    </div>
  );
};

export default Contact;
