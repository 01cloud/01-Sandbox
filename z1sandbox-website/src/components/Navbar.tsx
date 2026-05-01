import { useState } from "react";
import { Menu, X, LogIn, LogOut, LayoutDashboard, User } from "lucide-react";
import { useAuth0 } from "@auth0/auth0-react";
import ThemeToggle from "./ThemeToggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

const links = [
  { href: "/#pillars", label: "Pillars" },
  { href: "/#features", label: "Features" },
  { href: "/#security", label: "Security" },
  // { href: "/#architecture", label: "Architecture" },
  { href: "/#why", label: "Why 01 Sandbox" },
  { href: "/contact", label: "Contact" },
];

const Navbar = () => {
  const [open, setOpen] = useState(false);
  const { loginWithRedirect, logout, isAuthenticated, user, isLoading } = useAuth0();

  return (
    <nav className="fixed top-6 left-1/2 -translate-x-1/2 z-50 w-[min(1200px,calc(100%-2rem))] sm:w-[min(1200px,calc(100%-3rem))] transition-all duration-300">
      <div className={`relative border border-white/10 bg-background/60 backdrop-blur-2xl shadow-[0_8px_32px_-12px_rgba(0,0,0,0.5)] transition-all duration-500 overflow-hidden ${open ? 'rounded-[2.5rem]' : 'rounded-full'}`}>
        <div className="absolute inset-0 bg-gradient-to-tr from-primary/5 via-transparent to-white/5 pointer-events-none" />
        <div className="px-6 sm:px-8 flex items-center justify-between h-16 sm:h-18">
          <a href="/" className="font-display font-extrabold text-xl tracking-tight flex items-center gap-3 transition-opacity hover:opacity-90">
            <div className="w-10 h-10 rounded-[10px] bg-foreground/5 flex items-center justify-center shadow-sm ring-1 ring-border/50 overflow-hidden p-1.5 transition-transform group-hover:scale-110">
              <img
                src="/01cloud.png"
                alt="01Cloud Logo"
                className="w-full h-full object-contain dark:brightness-110"
              />
            </div>
            <span className="hidden sm:inline bg-gradient-to-br from-foreground to-foreground/70 bg-clip-text text-transparent transform-gpu">Sandbox</span>
          </a>

          <div className="hidden md:flex items-center gap-1 xl:gap-2 text-sm font-medium">
            {links.map((l) => (
              <a 
                key={l.href} 
                href={l.href} 
                className="px-4 py-2 rounded-xl text-muted-foreground/80 hover:text-foreground hover:bg-foreground/[0.03] transition-all relative group"
              >
                {l.label}
                <span className="absolute bottom-1.5 left-4 right-4 h-0.5 bg-primary/40 scale-x-0 group-hover:scale-x-100 transition-transform origin-left" />
              </a>
            ))}
          </div>

          <div className="hidden md:flex items-center gap-3">
            <ThemeToggle />
            <div className="w-px h-5 bg-border mx-1" />
            
            {!isLoading && (
              <>
                {isAuthenticated ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger className="focus:outline-none">
                      <div className="flex items-center gap-2 p-1 pr-3 rounded-full bg-foreground/[0.03] border border-border/50 hover:bg-foreground/[0.05] transition-all">
                        <Avatar className="w-8 h-8 border border-border/50">
                          <AvatarImage src={user?.picture} alt={user?.name} />
                          <AvatarFallback><User className="w-4 h-4" /></AvatarFallback>
                        </Avatar>
                        <span className="text-xs font-bold truncate max-w-[80px]">{user?.given_name || user?.name?.split(' ')[0]}</span>
                      </div>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56 mt-4 rounded-[1.5rem] p-2 backdrop-blur-2xl bg-background/90 border-white/10 shadow-2xl">
                      <DropdownMenuLabel className="px-3 py-3">
                        <div className="flex flex-col gap-0.5">
                          <p className="text-sm font-black tracking-tight">{user?.name}</p>
                          <p className="text-[10px] text-muted-foreground truncate uppercase tracking-[0.15em] font-bold opacity-50">{user?.email}</p>
                        </div>
                      </DropdownMenuLabel>
                      <DropdownMenuSeparator className="bg-border/50 mx-2" />
                      <DropdownMenuItem className="rounded-xl px-3 py-3 focus:bg-primary/10 focus:text-primary font-bold cursor-pointer" asChild>
                        <a href="/dashboard" className="flex items-center gap-3">
                          <LayoutDashboard className="w-4 h-4" />
                          Management Console
                        </a>
                      </DropdownMenuItem>
                      <DropdownMenuSeparator className="bg-border/50 mx-2" />
                      <DropdownMenuItem 
                        className="rounded-xl px-3 py-3 focus:bg-destructive/10 focus:text-destructive font-bold cursor-pointer text-destructive/80"
                        onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}
                      >
                        <LogOut className="w-4 h-4 mr-3" />
                        Sign Out
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : (
                  <button
                    onClick={() => loginWithRedirect()}
                    className="text-sm font-bold text-muted-foreground/80 hover:text-foreground transition-all px-5 py-2 hover:bg-foreground/[0.05] rounded-full flex items-center gap-2"
                  >
                    <LogIn className="w-4 h-4" />
                    Sign In
                  </button>
                )}
              </>
            )}

            <a
              href="/book-a-demo"
              className="group relative inline-flex items-center justify-center px-6 py-2.5 rounded-full bg-foreground text-background font-bold text-sm transition-all hover:scale-[1.05] active:scale-95 overflow-hidden"
            >
              <div className="absolute inset-0 bg-gradient-to-r from-primary via-accent to-primary opacity-0 group-hover:opacity-100 transition-opacity duration-500 animate-gradient" />
              <span className="relative">Book a Demo</span>
            </a>
          </div>

          <div className="md:hidden flex items-center gap-4">
            <ThemeToggle />
            <button
              className="w-10 h-10 flex items-center justify-center rounded-full bg-secondary/50 text-foreground transition-colors hover:bg-secondary"
              onClick={() => setOpen(!open)}
              aria-label="Menu"
            >
              {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>

        <div className={`grid transition-all duration-500 ease-in-out ${open ? 'grid-rows-[1fr] opacity-100 py-4' : 'grid-rows-[0fr] opacity-0'} max-h-[calc(100vh-6rem)] overflow-y-auto custom-scrollbar`}>
          <div className="min-h-0 overflow-hidden">
            <div className="px-6 sm:px-8 pb-8 pt-2 flex flex-col gap-5">
              <div className="grid grid-cols-2 gap-x-4 gap-y-4">
                {links.map((l) => (
                  <a key={l.href} href={l.href} onClick={() => setOpen(false)} className="text-base font-semibold text-muted-foreground hover:text-foreground transition-colors p-3 rounded-2xl bg-secondary/30">
                    {l.label}
                  </a>
                ))}
              </div>
              <div className="pt-6 border-t border-border/50 flex flex-col gap-4">
                {!isLoading && (
                  <>
                    {isAuthenticated ? (
                      <a 
                        href="/dashboard" 
                        onClick={() => setOpen(false)} 
                        className="w-full py-4 px-6 flex items-center justify-between text-base font-bold text-foreground rounded-2xl border border-border/50 bg-secondary/20"
                      >
                        <div className="flex items-center gap-3">
                          <Avatar className="w-8 h-8">
                            <AvatarImage src={user?.picture} />
                            <AvatarFallback><User className="w-4 h-4" /></AvatarFallback>
                          </Avatar>
                          <span>Dashboard</span>
                        </div>
                        <LayoutDashboard className="w-5 h-5 text-muted-foreground" />
                      </a>
                    ) : (
                      <button 
                        onClick={() => { loginWithRedirect(); setOpen(false); }} 
                        className="w-full py-4 text-center text-base font-bold text-muted-foreground hover:text-foreground rounded-2xl border border-border/50 flex items-center justify-center gap-2"
                      >
                        <LogIn className="w-5 h-5" />
                        Sign in
                      </button>
                    )}
                  </>
                )}
                <a
                  href="/book-a-demo"
                  onClick={() => setOpen(false)}
                  className="w-full py-4 text-center rounded-2xl pill-badge text-foreground font-extrabold shadow-xl text-lg"
                >
                  Book a Demo
                </a>
                {isAuthenticated && (
                   <button 
                    onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}
                    className="w-full py-3 text-center text-sm font-bold text-destructive/80 hover:text-destructive"
                  >
                    Sign Out
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
