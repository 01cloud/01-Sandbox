import { motion } from "framer-motion";

const Terms = () => {
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
              Terms of <span className="text-4xl md:text-6xl font-display font-black tracking-tight mb-8">Service</span>
            </h1>
            <p className="text-sm text-muted-foreground mb-12 uppercase tracking-[0.2em] font-bold">Effective Date: April 2026</p>

            <div className="space-y-12 prose prose-invert max-w-none">
              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">1. Agreement to Terms</h2>
                <p className="text-muted-foreground leading-relaxed">
                  By accessing or using 01 Sandbox, you agree to be bound by these Terms of Service. If you do not agree, you may not use our services.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">2. Prohibited Content & Use</h2>
                <p className="text-muted-foreground leading-relaxed mb-4">
                  You agree NOT to use the sandbox for:
                </p>
                <ul className="list-disc pl-6 space-y-3 text-muted-foreground">
                  <li>Developing, executing, or distributing malware, ransomware, or any harmful software.</li>
                  <li>Performing unauthorized security research or DDoS attacks on third-party systems.</li>
                  <li>Circumventing kernel-level isolation (gVisor) or accessing host infrastructure.</li>
                  <li>Engaging in illegal activities or violating the rights of others.</li>
                </ul>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">3. Service Availability & Liability</h2>
                <p className="text-muted-foreground leading-relaxed">
                  01 Sandbox is provided on an "as is" and "as available" basis. We do not guarantee uninterrupted service. We are not liable for any damages resulting from the execution of code within our pods or for any loss of data during temporary execution cycles.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">4. Intellectual Property</h2>
                <p className="text-muted-foreground leading-relaxed">
                  The 01 Sandbox platform, including all proprietary software, designs, and logos, is the exclusive property of our team. Users retain full ownership of the code they submit for execution.
                </p>
              </section>

              <section>
                <h2 className="text-2xl font-black tracking-tight mb-4 text-foreground">5. Termination</h2>
                <p className="text-muted-foreground leading-relaxed">
                  We reserve the right to suspend or terminate access to our services at any time, with or without notice, particularly in cases of suspected platform abuse or violations of these terms.
                </p>
              </section>
            </div>
          </motion.div>
        </div>
      </main>
    </div>
  );
};

export default Terms;
