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
import Health from "./pages/Health.tsx";
import RepoScanner from "./pages/RepoScanner.tsx";
import Metrics from "./pages/Metrics.tsx";


import CookieBanner from "./components/CookieBanner.tsx";
import { Auth0Provider } from "@auth0/auth0-react";
import { useNavigate } from "react-router-dom";

const queryClient = new QueryClient();

import React, { Component, ErrorInfo, ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  public state: ErrorBoundaryState = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught React Error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex flex-col items-center justify-center bg-background text-foreground p-6 text-center">
          <div className="max-w-md p-8 rounded-3xl border border-destructive/30 bg-destructive/5 space-y-4">
            <h2 className="text-xl font-bold text-destructive">Dashboard Error</h2>
            <p className="text-xs text-muted-foreground">
              {this.state.error?.message || "An unexpected error occurred while rendering the dashboard."}
            </p>
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null });
                window.location.href = "/dashboard";
              }}
              className="px-4 py-2 rounded-xl bg-violet-600 text-white font-bold text-xs hover:bg-violet-500 transition-all shadow-md"
            >
              Reload Management Console
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export const Auth0ProviderWithHistory = ({ children }: { children: React.ReactNode }) => {
  const navigate = useNavigate();

  const domain = (window as any)._env_?.VITE_AUTH0_DOMAIN || import.meta.env.VITE_AUTH0_DOMAIN || "";
  const clientId = (window as any)._env_?.VITE_AUTH0_CLIENT_ID || import.meta.env.VITE_AUTH0_CLIENT_ID || "";
  const audience = (window as any)._env_?.VITE_AUTH0_AUDIENCE || import.meta.env.VITE_AUTH0_AUDIENCE || "";

  if (!domain || !clientId) {
    console.warn("Auth0 configuration missing: domain or clientId not set. Rendering in offline/mock mode.");
    return <ErrorBoundary>{children}</ErrorBoundary>;
  }

  const onRedirectCallback = (appState: any) => {
    navigate(appState?.returnTo || "/dashboard");
  };

  return (
    <ErrorBoundary>
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
    </ErrorBoundary>
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

            {/* Global Background Elements for depth in Light Mode only... */}
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
                <Route path="/health" element={<Health />} />
                <Route path="/repo-scanner" element={<RepoScanner />} />
                <Route path="/metrics" element={<Metrics />} />
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
