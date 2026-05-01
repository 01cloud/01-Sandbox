import { motion } from "framer-motion";
import { useState } from "react";
import { CheckCircle2, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import ReCAPTCHA from "react-google-recaptcha";
import { useRef } from "react";

const BookDemo = () => {
  const recaptchaRef = useRef<ReCAPTCHA>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formData, setFormData] = useState({
    fullName: "",
    email: "",
    phone: "",
    accountType: "business",
    jobTitle: "",
    context: "",
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    const loadingToast = toast.loading("Verifying and sending request...");

    const recaptchaValue = recaptchaRef.current?.getValue();
    if (!recaptchaValue) {
      toast.dismiss(loadingToast);
      toast.error("Please complete the reCAPTCHA verification.");
      setIsSubmitting(false);
      return;
    }

    try {
      // Connecting to Google Chat Space Webhook
      const webhookUrl = import.meta.env.VITE_GOOGLE_CHAT_WEBHOOK_URL;
      if (!webhookUrl) {
        throw new Error("Webhook URL is not configured");
      }

      const messageText = `*New Demo Request* 🚀\n\n*Name:* ${formData.fullName}\n*Email:* ${formData.email}\n*Phone:* ${formData.phone || 'N/A'}\n*Account Type:* ${formData.accountType}\n*Job Title:* ${formData.jobTitle}\n*Context:* ${formData.context || 'N/A'}\n*reCAPTCHA Verified:* Yes`;

      const response = await fetch(webhookUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          text: messageText,
        }),
      });

      if (response.ok) {
        toast.dismiss(loadingToast);
        toast.success("Your request has been successfully submitted. A member of our team will review it and get in touch with you shortly.");
        recaptchaRef.current?.reset();

        setFormData({
          fullName: "",
          email: "",
          phone: "",
          accountType: "business",
          jobTitle: "",
          context: "",
        });
      } else {
        throw new Error("Form submission failed");
      }
    } catch (error) {
      toast.dismiss(loadingToast);
      toast.error("Something went wrong. Please check your connection and try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const benefits = [
    { title: "Hardened Isolation", desc: "Execute any AI-generated code in kernel-isolated pods that prevent access to host resources and peer containers." },
    { title: "Real-time Security Toolchain", desc: "Every execution job is automatically audited by Semgrep, Gitleaks, and Bandit to detect vulnerabilities at the edge." },
    { title: "High-Concurrency Low Latency", desc: "Optimized for AI agent workflows with millisecond-level pod provisioning and synchronous result delivery." },
  ];

  return (
    <div className="min-h-screen">

      <main className="pt-32 pb-20">
        <div className="container mx-auto px-6">
          <div className="grid lg:grid-cols-2 gap-16 items-start max-w-6xl mx-auto">

            {/* Left Content */}
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.6 }}
            >
              <h1 className="text-5xl md:text-7xl font-display font-black tracking-tight leading-[1.1] mb-6">
                Request a <br />
                <span className="text-gradient">Live Demo</span>
              </h1>
              <p className="text-xl text-muted-foreground leading-relaxed mb-12 max-w-lg">
                Securely execute untrusted code in hardened, isolated environments. Discover how Agent Sandbox provides the infrastructure for safe AI agent autonomy.
              </p>

              <div className="space-y-8 mb-12">
                {benefits.map((benefit) => (
                  <div key={benefit.title} className="flex gap-4">
                    <div className="mt-1">
                      <CheckCircle2 className="w-6 h-6 text-accent" />
                    </div>
                    <div>
                      <h3 className="font-bold text-lg text-foreground mb-1">{benefit.title}</h3>
                      <p className="text-muted-foreground text-sm leading-relaxed">{benefit.desc}</p>
                    </div>
                  </div>
                ))}
              </div>

              <div className="p-8 rounded-[2rem] bg-secondary/30 border border-border/50 backdrop-blur-sm max-w-md">
                <h4 className="text-xs font-black uppercase tracking-[0.2em] text-foreground mb-4">What to expect</h4>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  A deep dive into our isolation technology, synchronous security toolchain, and how to integrate Agent Sandbox into your multi-tenant application workflows.
                </p>
              </div>
            </motion.div>

            {/* Right Form */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.2 }}
              className="bg-card border border-border p-8 md:p-10 rounded-[2.5rem] shadow-2xl relative overflow-hidden"
            >
              <div className="absolute top-0 right-0 w-64 h-64 bg-accent/5 rounded-full blur-3xl -mr-32 -mt-32" />

              <form onSubmit={handleSubmit} className="relative z-10 space-y-6">
                <div className="space-y-2">
                  <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Full Name *</label>
                  <input
                    required
                    type="text"
                    placeholder="John Doe"
                    className="w-full px-5 py-4 rounded-2xl bg-secondary/50 border border-border focus:border-accent/50 focus:ring-4 focus:ring-accent/5 outline-none transition-all placeholder:text-muted-foreground/30"
                    value={formData.fullName}
                    onChange={(e) => setFormData({ ...formData, fullName: e.target.value })}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Work Email *</label>
                  <input
                    required
                    type="email"
                    placeholder="john@company.com"
                    className="w-full px-5 py-4 rounded-2xl bg-secondary/50 border border-border focus:border-accent/50 focus:ring-4 focus:ring-accent/5 outline-none transition-all placeholder:text-muted-foreground/30"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  />
                </div>

                <div className="grid md:grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Phone Number</label>
                    <input
                      type="tel"
                      placeholder="+1 (555) 000-0000"
                      className="w-full px-5 py-4 rounded-2xl bg-secondary/50 border border-border focus:border-accent/50 focus:ring-4 focus:ring-accent/5 outline-none transition-all placeholder:text-muted-foreground/30"
                      value={formData.phone}
                      onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Account Type *</label>
                    <div className="flex gap-4 p-1 rounded-2xl bg-secondary/50 border border-border h-[58px]">
                      {['personal', 'business'].map((type) => (
                        <button
                          key={type}
                          type="button"
                          onClick={() => setFormData({ ...formData, accountType: type })}
                          className={`flex-1 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${formData.accountType === type
                            ? 'bg-foreground text-background shadow-lg'
                            : 'text-muted-foreground hover:text-foreground'
                            }`}
                        >
                          {type}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Job Title *</label>
                  <input
                    required
                    type="text"
                    placeholder="Security Researcher"
                    className="w-full px-5 py-4 rounded-2xl bg-secondary/50 border border-border focus:border-accent/50 focus:ring-4 focus:ring-accent/5 outline-none transition-all placeholder:text-muted-foreground/30"
                    value={formData.jobTitle}
                    onChange={(e) => setFormData({ ...formData, jobTitle: e.target.value })}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Additional Context</label>
                  <textarea
                    rows={3}
                    placeholder="Tell us about your security goals..."
                    className="w-full px-5 py-4 rounded-2xl bg-secondary/50 border border-border focus:border-accent/50 focus:ring-4 focus:ring-accent/5 outline-none transition-all placeholder:text-muted-foreground/30 resize-none"
                    value={formData.context}
                    onChange={(e) => setFormData({ ...formData, context: e.target.value })}
                  />
                </div>

                {/* Honeypot field - Invisible to humans, catches bots */}
                <input type="text" name="_gotcha" style={{ display: 'none' }} />

                <div className="flex justify-center py-2">
                  <ReCAPTCHA
                    ref={recaptchaRef}
                    sitekey={import.meta.env.VITE_RECAPTCHA_SITE_KEY || "YOUR_SITE_KEY_MISSING"}
                    theme="dark"
                  />
                </div>

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className={`w-full py-5 rounded-2xl bg-gradient-to-r from-[hsl(var(--grad-3))] to-[hsl(var(--grad-4))] text-white font-black text-lg shadow-xl shadow-accent/20 hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2 group ${isSubmitting ? 'opacity-70 cursor-not-allowed' : ''
                    }`}
                >
                  {isSubmitting ? "Sending..." : "Request Demo"}
                  {!isSubmitting && <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />}
                </button>
              </form>
            </motion.div>
          </div>
        </div>
      </main>
    </div>
  );
};

export default BookDemo;
