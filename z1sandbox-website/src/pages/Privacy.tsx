import { motion } from "framer-motion";

const Privacy = () => {
  return (
    <div className="min-h-screen text-foreground selection:bg-accent selection:text-white">
      <main className="pt-40 pb-32">
        <div className="container mx-auto px-6 max-w-4xl">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <h1 className="text-4xl md:text-6xl font-display font-black tracking-tight mb-8">
              Privacy <span className="text-4xl md:text-6xl font-display font-black tracking-tight mb-8">Policy</span>
            </h1>
            <p className="text-sm text-muted-foreground mb-12 uppercase tracking-[0.2em] font-bold">Last Updated: April 2026</p>

            <div className="space-y-12 prose prose-invert max-w-none">
              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">1. Introduction</h2>
                <p className="text-muted-foreground leading-relaxed">
                  01 Sandbox is committed to protecting your privacy. This Privacy Policy explains how we collect, use, and protect your information when you use our secure code execution platform.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">2. Data Collection & Isolation</h2>
                <p className="text-muted-foreground leading-relaxed mb-4">
                  Our primary mission is to provide secure isolation. We collect the following types of information:
                </p>
                <ul className="list-disc pl-6 space-y-3 text-muted-foreground">
                  <li><strong className="text-foreground">Execution Metadata:</strong> Logs including execution time, resource usage, and exit codes.</li>
                  <li><strong className="text-foreground">Authentication Data:</strong> API keys and identifiers used to authorize jobs.</li>
                  <li><strong className="text-foreground">Temporary Code Storage:</strong> Code snippets are stored temporarily in isolated hardened pods and are destroyed immediately upon job completion.</li>
                </ul>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">3. Security Auditing</h2>
                <p className="text-muted-foreground leading-relaxed">
                  Every execution job may be automatically scanned for security vulnerabilities (e.g., via Semgrep or Gitleaks). These audit records are maintained solely to provide you with execution reports and to ensure the integrity of our infrastructure.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">4. Data Third-Party Sharing</h2>
                <p className="text-muted-foreground leading-relaxed">
                  We do not sell your data. We only share information with third-party service providers (such as cloud infrastructure providers) as necessary to provide our services, and always under strict confidentiality agreements.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">5. Your Rights</h2>
                <p className="text-muted-foreground leading-relaxed">
                  You have the right to access, correct, or delete your account information at any time. For concerns regarding your data, please contact our privacy team at <a href="mailto:info@01security.com" className="text-accent hover:underline">info@01security.com</a>.
                </p>
              </section>
            </div>
          </motion.div>
        </div>
      </main>
    </div>
  );
};

export default Privacy;
