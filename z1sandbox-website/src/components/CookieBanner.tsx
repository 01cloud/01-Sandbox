import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Cookie, ShieldCheck, Settings2, Check, ArrowLeft } from "lucide-react";
import { toast } from "sonner";

const CookieBanner = () => {
  const [isVisible, setIsVisible] = useState(false);
  const [isSettingsView, setIsSettingsView] = useState(false);
  const [settings, setSettings] = useState({
    essential: true,
    analytics: true,
    marketing: false,
  });

  useEffect(() => {
    const consent = localStorage.getItem("cookie-consent");
    if (!consent) {
      const timer = setTimeout(() => setIsVisible(true), 2000);
      return () => clearTimeout(timer);
    }
  }, []);

  const handleSave = (type: "all" | "rejected" | "custom") => {
    let finalSettings = settings;
    if (type === "all") finalSettings = { essential: true, analytics: true, marketing: true };
    if (type === "rejected") finalSettings = { essential: true, analytics: false, marketing: false };
    
    localStorage.setItem("cookie-consent", JSON.stringify(finalSettings));
    setIsVisible(false);
    toast.success("Preferences saved successfully");
  };

  const toggleSetting = (key: keyof typeof settings) => {
    if (key === "essential") return;
    setSettings(prev => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <AnimatePresence>
      {isVisible && (
        <div className="fixed inset-0 z-[100] flex items-end justify-center md:justify-end p-6 pointer-events-none">
          <motion.div
            initial={{ opacity: 0, y: 100, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 100, scale: 0.95 }}
            className="w-full max-w-md bg-card border border-border shadow-2xl p-8 rounded-[2.5rem] pointer-events-auto relative overflow-hidden"
          >
            {/* Background Accent */}
            <div className="absolute top-0 right-0 w-32 h-32 bg-accent/5 rounded-full blur-[60px] -mr-16 -mt-16" />
            
            <button 
              onClick={() => setIsVisible(false)}
              className="absolute top-4 right-4 p-2 rounded-full hover:bg-secondary/50 text-muted-foreground transition-colors"
            >
              <X className="w-4 h-4" />
            </button>

            <AnimatePresence mode="wait">
              {!isSettingsView ? (
                <motion.div
                  key="main"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  className="flex flex-col gap-6"
                >
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-2xl bg-accent/10 flex items-center justify-center text-accent">
                      <Cookie className="w-6 h-6" />
                    </div>
                    <div>
                      <h3 className="font-display font-black tracking-tight text-xl text-foreground">Cookie Settings</h3>
                      <p className="text-[10px] uppercase font-black tracking-widest text-muted-foreground/60">Privacy Compliance</p>
                    </div>
                  </div>

                  <p className="text-sm text-muted-foreground leading-relaxed">
                    We use cookies and similar technologies to help personalize content, measure functional efficiency, and provide a safer user experience. By clicking <span className="text-foreground font-bold">"Accept All"</span>, you agree to our use of these tools for performance, analytics, and marketing. You can adjust your preferences at any time.
                  </p>

                  <div className="flex flex-col gap-3">
                    <button
                      onClick={() => handleSave("all")}
                      className="w-full py-4 rounded-2xl bg-foreground text-background font-black text-sm hover:scale-[1.02] transition-all flex items-center justify-center gap-2 group"
                    >
                      <ShieldCheck className="w-4 h-4" />
                      Accept All Cookies
                    </button>
                    
                    <button
                      onClick={() => handleSave("rejected")}
                      className="w-full py-4 rounded-2xl bg-secondary/30 border border-border text-foreground font-black text-sm hover:bg-secondary/50 transition-all"
                    >
                      Reject All
                    </button>

                    <button
                      onClick={() => setIsSettingsView(true)}
                      className="w-full py-4 rounded-2xl bg-transparent border border-border/50 text-muted-foreground/60 font-black text-sm hover:text-foreground hover:border-foreground/20 transition-all flex items-center justify-center gap-2"
                    >
                      <Settings2 className="w-4 h-4" />
                      Customize Settings
                    </button>
                  </div>
                </motion.div>
              ) : (
                <motion.div
                  key="settings"
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -20 }}
                  className="flex flex-col gap-6"
                >
                  <button 
                    onClick={() => setIsSettingsView(false)}
                    className="flex items-center gap-2 text-[10px] uppercase font-black tracking-[0.2em] text-muted-foreground hover:text-foreground transition-colors mb-2"
                  >
                    <ArrowLeft className="w-3 h-3" />
                    Back to overview
                  </button>

                  <div className="space-y-4">
                    {[
                      { 
                        id: 'essential', 
                        label: 'Essential', 
                        desc: "Critical for the platform's core security. They manage encrypted session tokens, authentication, and your UI preferences. These cannot be disabled.", 
                        disabled: true 
                      },
                      { 
                        id: 'analytics', 
                        label: 'Analytics', 
                        desc: 'Provides anonymous insights into platform performance, job execution latency, and page usage trends to help us optimize our gVisor infrastructure.', 
                        disabled: false 
                      },
                      { 
                        id: 'marketing', 
                        label: 'Marketing', 
                        desc: 'Helps us deliver relevant security updates and feature announcements via LinkedIn and X, and allows us to track the effectiveness of our industry outreach.', 
                        disabled: false 
                      },
                    ].map((item) => (
                      <div 
                        key={item.id}
                        onClick={() => !item.disabled && toggleSetting(item.id as keyof typeof settings)}
                        className={`p-4 rounded-2xl border transition-all cursor-pointer ${
                          settings[item.id as keyof typeof settings]
                            ? 'bg-accent/5 border-accent/20'
                            : 'bg-secondary/10 border-border/30 grayscale opacity-60'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <h4 className="font-bold text-sm">{item.label}</h4>
                          <div className={`w-5 h-5 rounded-md flex items-center justify-center transition-all ${
                            settings[item.id as keyof typeof settings] ? 'bg-accent text-white scale-110' : 'bg-border text-transparent scale-90'
                          }`}>
                            <Check className="w-3 h-3" />
                          </div>
                        </div>
                        <p className="text-[11px] text-muted-foreground leading-snug">{item.desc}</p>
                      </div>
                    ))}
                  </div>

                  <button
                    onClick={() => handleSave("custom")}
                    className="w-full py-5 rounded-2xl bg-foreground text-background font-black text-[14px] hover:scale-[1.02] transition-all mt-2"
                  >
                    Save Preferences
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default CookieBanner;
