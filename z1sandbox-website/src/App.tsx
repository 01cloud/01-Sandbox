import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import Index from "./pages/Index.tsx";
import BookDemo from "./pages/BookDemo.tsx";
import Contact from "./pages/Contact.tsx";
import Privacy from "./pages/Privacy.tsx";
import Terms from "./pages/Terms.tsx";
import NotFound from "./pages/NotFound.tsx";
import Dashboard from "./pages/Dashboard.tsx";
import CookieBanner from "./components/CookieBanner.tsx";
import { Auth0Provider } from "@auth0/auth0-react";
import { useNavigate } from "react-router-dom";

const queryClient = new QueryClient();

const Auth0ProviderWithHistory = ({ children }: { children: React.ReactNode }) => {
  const navigate = useNavigate();
  
  const domain = (window as any)._env_?.VITE_AUTH0_DOMAIN || import.meta.env.VITE_AUTH0_DOMAIN || "dev-axwc0ui527kw0c5d.us.auth0.com";
  const clientId = (window as any)._env_?.VITE_AUTH0_CLIENT_ID || import.meta.env.VITE_AUTH0_CLIENT_ID || "sqVN3z2Er7YxXYK4FxbpOaYOqL2ju22D";
  const audience = (window as any)._env_?.VITE_AUTH0_AUDIENCE || import.meta.env.VITE_AUTH0_AUDIENCE || "https://code-inspector-api";

  const onRedirectCallback = (appState: any) => {
    navigate(appState?.returnTo || "/dashboard");
  };

  return (
    <Auth0Provider
      domain={domain}
      clientId={clientId}
      authorizationParams={{
        redirect_uri: window.location.origin,
        audience: audience,
        scope: "openid profile email"
      }}
      onRedirectCallback={onRedirectCallback}
    >
      {children}
    </Auth0Provider>
  );
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <TooltipProvider>
        <Toaster />
        <Sonner />
        <Auth0ProviderWithHistory>
          <div className="relative flex min-h-screen flex-col overflow-x-hidden">

            {/* Global Background Elements for depth in Light Mode only */}
            <div className="fixed inset-0 pointer-events-none -z-10 bg-background dark:hidden">
              <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-[hsl(var(--grad-3))] mix-blend-multiply opacity-[0.05] blur-[100px]" />
              <div className="absolute top-[20%] right-[-5%] w-[40%] h-[40%] rounded-full bg-[hsl(var(--grad-4))] mix-blend-multiply opacity-[0.05] blur-[120px]" />
            </div>

            <Navbar />
            <CookieBanner />
            <main className="flex-1">
              <Routes>
                <Route path="/" element={<Index />} />
                <Route path="/book-a-demo" element={<BookDemo />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/contact" element={<Contact />} />
                <Route path="/privacy" element={<Privacy />} />
                <Route path="/terms" element={<Terms />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </main>
            <Footer />
          </div>
        </Auth0ProviderWithHistory>
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>
);


export default App;
