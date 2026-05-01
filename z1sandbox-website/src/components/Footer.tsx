const Footer = () => {
  return (
    <footer className="py-12 bg-background border-t border-border/50 relative overflow-hidden">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-px bg-gradient-to-r from-transparent via-border/50 to-transparent" />

      <div className="container mx-auto px-6">
        <div className="flex flex-col md:flex-row items-center justify-between gap-10">
          {/* Brand Section */}
          <a href="/" className="flex items-center gap-3 hover:opacity-90 transition-opacity">
            <div className="w-8 h-8 rounded-lg bg-foreground/5 flex items-center justify-center p-1.5 ring-1 ring-border/50">
              <img
                src="/01cloud.png"
                alt="01 logo"
                className="w-full h-full object-contain dark:brightness-110"
              />
            </div>
            <span className="font-display font-black text-xl tracking-tighter text-gradient">
              Sandbox
            </span>
          </a>

          {/* Copyright Section */}
          <div className="text-[10px] text-muted-foreground font-bold uppercase tracking-[0.2em]">
            © {new Date().getFullYear()} 01 Security. All rights reserved.
          </div>

          {/* Links Section */}
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-4 text-[11px] font-bold uppercase tracking-widest text-muted-foreground/60">
            <a href="/privacy" className="hover:text-foreground transition-colors">Privacy Policy</a>
            <a href="/terms" className="hover:text-foreground transition-colors">Terms of Use</a>
            <a href="/contact" className="hover:text-foreground transition-colors">Contact Us</a>
          </div>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
