import { motion } from "framer-motion";
import { Mail, MessageSquare, Github, Globe, Linkedin, Calendar } from "lucide-react";

const XIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 300 300" className={className} fill="currentColor">
    <path d="M178.57 127.15 290.27 0h-26.46l-97.03 110.38L89.34 0H0l117.13 166.93L0 300.25h26.46l102.4-116.59 81.8 116.59h89.34M36.01 19.54H76.66l187.13 262.13h-40.66" />
  </svg>
);

const Contact = () => {
  const contactMethods = [
    { label: "Book a Demo", icon: Calendar, href: "/book-a-demo", desc: "Schedule a walkthrough" },
    // { label: "GitHub", icon: Github, href: "https://github.com/11security", desc: "01 Sandbox" },
    // { label: "Slack", icon: MessageSquare, href: "https://join.slack.com/t/01securitycommunity/shared_invite/zt-2d1f1997c-~yJz7LNDkU5W2b4B~o~h0A", desc: "01 Security" },
  ];

  return (
    <div className="relative min-h-screen text-foreground selection:bg-accent selection:text-white overflow-hidden">
      {/* Background Decorative Orbs - More Subtle */}
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none -z-10">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-accent/2 rounded-full blur-[120px] animate-pulse" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-primary/2 rounded-full blur-[120px] animate-pulse" style={{ animationDelay: '1s' }} />
      </div>

      <main className="pt-40 pb-32">
        <div className="container mx-auto px-6 max-w-7xl">

          {/* Minimalist Header */}
          <div className="text-center mb-24">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-accent/5 border border-accent/10 text-accent text-[11px] font-bold uppercase tracking-[0.2em] mb-8"
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
              className="text-lg md:text-xl text-muted-foreground/60 max-w-xl mx-auto leading-relaxed"
            >
              Building the future of secure code execution. <br className="hidden md:block" /> Reach out via our official channels.
            </motion.p>
          </div>

          {/* Clean Card Grid - Centered Content & Darker Style */}
          <div className="flex flex-wrap justify-center gap-6 md:gap-8 mb-32">
            {contactMethods.map((method, idx) => (
              <motion.a
                key={method.label}
                href={method.href}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.3 + idx * 0.1 }}
                className="group relative p-10 md:p-12 rounded-[2.5rem] bg-card/20 backdrop-blur-2xl border border-border/20 hover:bg-card/40 hover:border-accent/20 transition-all duration-500 overflow-hidden min-w-[280px] sm:min-w-[320px] flex-1 max-w-[400px]"
              >
                <div className="relative z-10 flex flex-col items-center text-center">
                  <div className="w-16 h-16 rounded-2xl bg-foreground/5 flex items-center justify-center mb-8 group-hover:scale-110 group-hover:bg-accent group-hover:text-white transition-all duration-500 ring-1 ring-border/20 group-hover:ring-accent/40 shadow-lg shadow-transparent group-hover:shadow-accent/10">
                    <method.icon className="w-7 h-7" />
                  </div>
                  <h3 className="text-2xl font-bold tracking-tight mb-3 group-hover:text-foreground transition-colors">{method.label}</h3>
                  <p className="text-sm font-medium tracking-wide text-muted-foreground/40 group-hover:text-accent/80 transition-colors">
                    {method.desc}
                  </p>
                </div>
                {/* Decorative background glow */}
                <div className="absolute inset-0 bg-gradient-to-b from-accent/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                <div className="absolute -bottom-12 -right-12 w-32 h-32 bg-accent/5 rounded-full blur-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-700" />
              </motion.a>
            ))}
          </div>

          {/* Minimalist Office Section */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.5 }}
            className="flex flex-col items-center pt-24 border-t border-border/10"
          >
            <div className="flex items-center gap-2 mb-10 text-accent font-bold bg-accent/10 px-6 py-2 rounded-full border border-accent/20">
              <Globe className="w-4 h-4" />
              <span className="text-[10px] font-black uppercase tracking-[0.4em]">Global Headquarters</span>
            </div>

            <div className="text-center group cursor-default p-12 md:p-16 rounded-[3.5rem] bg-card/30 backdrop-blur-2xl border border-border/30 hover:border-accent/40 transition-all duration-700 shadow-2xl shadow-accent/5 max-w-2xl w-full">
              <h2 className="text-4xl md:text-5xl font-black tracking-tightest mb-6 text-foreground group-hover:text-accent transition-colors duration-500">
                United States
              </h2>
              <p className="text-muted-foreground/80 leading-relaxed text-xl font-medium max-w-md mx-auto">
                Z1 Security LLC<br />
                <span className="text-foreground/90 font-black not-italic mt-2 block">
                  10989 Tower PI, <br className="sm:hidden" />
                  Manassas, Virginia 20109
                </span>
              </p>

              <div className="mt-10 pt-10 border-t border-border/10 flex items-center justify-center gap-6">
                <div className="w-1.5 h-1.5 rounded-full bg-accent animate-ping" />
                <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/40">Verified Location</span>
              </div>
            </div>
          </motion.div>

        </div>
      </main>
    </div>
  );
};

export default Contact;
