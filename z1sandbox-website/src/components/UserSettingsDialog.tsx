import { useState, useEffect } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import {
  X,
  Key,
  ShieldCheck,
  Plus,
  Trash2,
  Copy,
  CheckCircle2,
  Clock,
  Calendar,
  User,
  Settings,
  Mail,
  Loader2
} from "lucide-react";
import { toast } from "sonner";
import DOMPurify from "dompurify";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/apiConfig";

const API_BASE_URL = getApiBaseUrl();

interface UserSettingsDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

interface APIKey {
  id: string;
  name: string;
  backend: string;
  created_at: string;
  expires_at: string;
  is_revoked: boolean;
}

export default function UserSettingsDialog({ isOpen, onClose }: UserSettingsDialogProps) {
  const { user, getAccessTokenSilently, isAuthenticated } = useAuth0();
  const [activeTab, setActiveTab] = useState<"profile" | "keys">("keys");
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<{ key: string; status?: string } | null>(null);
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "",
    backend: "Z1_SANDBOX",
    ttl: "days",
    ttlValue: "30",
  });

  const fetchKeys = async () => {
    try {
      setIsLoading(true);
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        console.warn("Auth0 not authenticated, using local mock keys:", err);
      }

      if (!token) {
        const stored = localStorage.getItem("local_mock_api_keys");
        if (stored) {
          setKeys(JSON.parse(stored));
        } else {
          setKeys([]);
        }
        return;
      }

      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        if (data.keys) {
          setKeys(data.keys.filter((k: APIKey) => !k.is_revoked));
        }
      }
    } catch (error) {
      console.error("Error fetching keys:", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchKeys();
      setNewKey(null);
    }
  }, [isOpen, isAuthenticated]);

  const handleCreateKey = async () => {
    const sanitizedName = DOMPurify.sanitize(form.name).trim();

    if (!sanitizedName) {
      toast.error("Please enter a valid key name");
      return;
    }

    try {
      setIsCreating(true);
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        console.warn("Auth0 not authenticated, using local mock key creation:", err);
      }

      let ttl_hours: number = -1;
      let ttl_seconds: number | null = null;
      if (form.ttl !== "never") {
        const val = parseInt(form.ttlValue);
        if (isNaN(val) || val <= 0) {
          toast.error("TTL must be a positive integer");
          return;
        }

        const multipliers: Record<string, number> = {
          minutes: 60,
          hours: 3600,
          days: 86400,
          months: 2592000,
          years: 31536000,
        };
        ttl_seconds = val * multipliers[form.ttl];
        ttl_hours = ttl_seconds / 3600;
      }

      if (!token) {
        // Local mock API key generation
        const mockKeyId = "key_" + Math.random().toString(36).substr(2, 9);
        const mockKeyValue = "z1_" + Math.random().toString(36).substr(2, 24);
        const mockKeyObj = {
          id: mockKeyId,
          name: sanitizedName,
          backend: form.backend,
          prefix: "z1_mock",
          created_at: new Date().toISOString(),
          expires_at: form.ttl === "never" ? "Never" : new Date(Date.now() + (ttl_seconds || 0) * 1000).toISOString(),
          is_revoked: false,
        };

        const stored = localStorage.getItem("local_mock_api_keys");
        const currentList = stored ? JSON.parse(stored) : [];
        currentList.push(mockKeyObj);
        localStorage.setItem("local_mock_api_keys", JSON.stringify(currentList));
        localStorage.setItem(`bound_key_${mockKeyId}`, mockKeyValue);

        setNewKey({ key: mockKeyValue });
        await fetchKeys();
        setForm({ name: "", backend: "Z1_SANDBOX", ttl: "days", ttlValue: "30" });
        toast.success("API Key generated successfully (Local Dev)!");
        window.dispatchEvent(new Event("api-keys-changed"));
        return;
      }

      const response = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          name: sanitizedName,
          backend: form.backend,
          ttl_hours,
          user_email: user?.email || undefined,
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to generate key");
      }

      const data = await response.json();
      setNewKey({ key: data.api_key });
      localStorage.setItem(`bound_key_${data.api_key_id}`, data.api_key);

      // Re-fetch local list
      await fetchKeys();
      setForm({ name: "", backend: "Z1_SANDBOX", ttl: "days", ttlValue: "30" });
      toast.success("API Key generated successfully!");

      // Dispatch global sync event
      window.dispatchEvent(new Event("api-keys-changed"));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevokeKey = async (id: string) => {
    try {
      let token = "";
      try {
        token = await getAccessTokenSilently();
      } catch (err) {
        // Fallback for local dev
      }

      if (!token) {
        const stored = localStorage.getItem("local_mock_api_keys");
        if (stored) {
          const currentList = JSON.parse(stored);
          const updatedList = currentList.filter((k: any) => k.id !== id);
          localStorage.setItem("local_mock_api_keys", JSON.stringify(updatedList));
        }
        setKeys(keys.filter((k) => k.id !== id));
        toast.info("Key revoked successfully (Local Dev)");
        window.dispatchEvent(new Event("api-keys-changed"));
        return;
      }

      await fetch(`${API_BASE_URL}/v1/api-keys/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      setKeys(keys.filter((k) => k.id !== id));
      toast.info("Key revoked successfully");

      // Dispatch global sync event
      window.dispatchEvent(new Event("api-keys-changed"));
    } catch (error) {
      toast.error("Failed to revoke key");
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard");
  };

  return (
    <>
      <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="max-w-[760px] w-[95vw] h-[80vh] rounded-3xl border border-border bg-background flex flex-col overflow-hidden p-0 shadow-2xl">
          {/* Header */}
          <DialogHeader className="px-8 py-5 border-b bg-muted/20 flex flex-row items-center justify-between space-y-0 shrink-0">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-2xl bg-violet-500/10 border border-violet-500/20 text-violet-500 shrink-0">
                <Settings className="w-5 h-5" />
              </div>
              <div>
                <DialogTitle className="text-lg font-black tracking-tight text-foreground">
                  User Console Settings
                </DialogTitle>
                <DialogDescription className="text-xs text-muted-foreground">
                  Manage your personal API keys and profile settings.
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          {/* Navigation Tabs */}
          <div className="px-8 py-3 border-b bg-muted/5 flex gap-4 shrink-0">
            <button
              onClick={() => setActiveTab("keys")}
              className={cn(
                "pb-2 text-sm font-bold border-b-2 transition-all flex items-center gap-2",
                activeTab === "keys"
                  ? "border-violet-500 text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Key className="w-4 h-4" />
              API Keys
            </button>
            <button
              onClick={() => setActiveTab("profile")}
              className={cn(
                "pb-2 text-sm font-bold border-b-2 transition-all flex items-center gap-2",
                activeTab === "profile"
                  ? "border-violet-500 text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <User className="w-4 h-4" />
              Profile
            </button>
          </div>

          {/* Content Area */}
          <div className="flex-1 overflow-hidden p-8 flex flex-col min-h-0">
            {activeTab === "keys" ? (
              <div className="flex-1 flex flex-col gap-6 overflow-hidden min-h-0">
                {/* Upper row: Actions */}
                <div className="flex justify-between items-center shrink-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-black text-sm uppercase tracking-wider text-muted-foreground">
                      Active Access Keys
                    </h3>
                    <Badge variant="outline" className={`rounded-full px-2 py-0.5 text-[10px] font-bold font-mono ${keys.length >= 5 ? 'bg-destructive/5 text-destructive border-destructive/20' : 'bg-emerald-500/5 text-emerald-500 border-emerald-500/20'}`}>
                      {keys.length} / 5 KEYS USED
                    </Badge>
                  </div>

                  <Dialog onOpenChange={(open) => { if (!open) setNewKey(null); }}>
                    <DialogTrigger asChild>
                      <Button className="rounded-2xl h-10 px-4 font-bold flex items-center gap-2 shadow-lg shadow-primary/10 text-xs">
                        <Plus className="w-4 h-4" />
                        Generate New Key
                      </Button>
                    </DialogTrigger>
                    <DialogContent className="sm:max-w-md rounded-[2.5rem] border-border/50 p-8">
                      <DialogHeader className="mb-6">
                        <DialogTitle className="text-2xl font-black">Generate API Key</DialogTitle>
                        <DialogDescription className="text-base">
                          Assign a specific backend and TTL for your new security identity.
                        </DialogDescription>
                      </DialogHeader>

                      {newKey ? (
                        <div className="space-y-6">
                          <div className="p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-500 flex items-start gap-4">
                            <CheckCircle2 className="w-5 h-5 mt-0.5 shrink-0" />
                            <div className="text-sm font-medium">
                              {newKey.status || "Key generated successfully. Copy it now, as it won't be shown again."}
                            </div>
                          </div>
                          <div
                            className="group relative p-6 rounded-2xl bg-zinc-950 text-emerald-400 font-mono text-sm break-all cursor-pointer hover:bg-zinc-900 transition-colors border border-white/5"
                            onClick={() => copyToClipboard(newKey.key)}
                          >
                            {newKey.key}
                            <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity">
                              <Copy className="w-4 h-4 text-emerald-400/50" />
                            </div>
                          </div>
                          <Button className="w-full rounded-2xl h-12 font-bold" onClick={() => copyToClipboard(newKey.key)}>
                            Copy to Clipboard
                          </Button>
                        </div>
                      ) : (
                        <div className="space-y-6">
                          <div className="space-y-2">
                            <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Key Name</label>
                            <Input
                              placeholder="e.g. Production Scanner"
                              className="rounded-xl h-12 border-border/50 focus-visible:ring-primary/20"
                              value={form.name}
                              onChange={(e) => setForm({ ...form, name: e.target.value })}
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Target Backend</label>
                            <Select value={form.backend} onValueChange={(val) => setForm({ ...form, backend: val })}>
                              <SelectTrigger className="rounded-xl h-12 border-border/50">
                                <SelectValue placeholder="Select Backend" />
                              </SelectTrigger>
                              <SelectContent className="rounded-xl border-border/50">
                                <SelectItem value="Z1_SANDBOX">Z1 Sandbox</SelectItem>
                                <SelectItem value="OPEN_SANDBOX">OpenSandbox</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="space-y-2">
                            <label className="text-xs font-black uppercase tracking-widest text-muted-foreground px-1">Time to Live</label>
                            <div className="flex gap-3">
                              <div className="flex-[0.4]">
                                <Input
                                  type="number"
                                  min="1"
                                  className="rounded-xl h-12 border-border/50 focus-visible:ring-primary/20"
                                  value={form.ttlValue}
                                  onChange={(e) => setForm({ ...form, ttlValue: e.target.value })}
                                  disabled={form.ttl === "never"}
                                />
                              </div>
                              <div className="flex-[0.6]">
                                <Select value={form.ttl} onValueChange={(val) => setForm({ ...form, ttl: val })}>
                                  <SelectTrigger className="rounded-xl h-12 border-border/50">
                                    <SelectValue placeholder="Unit" />
                                  </SelectTrigger>
                                  <SelectContent className="rounded-xl border-border/50">
                                    <SelectItem value="minutes">Minutes</SelectItem>
                                    <SelectItem value="hours">Hours</SelectItem>
                                    <SelectItem value="days">Days</SelectItem>
                                    <SelectItem value="months">Months</SelectItem>
                                    <SelectItem value="years">Years</SelectItem>
                                    <SelectItem value="never">Never Expire</SelectItem>
                                  </SelectContent>
                                </Select>
                              </div>
                            </div>
                          </div>
                          {keys.length >= 5 && (
                            <div className="p-4 rounded-2xl bg-destructive/5 border border-destructive/20 text-destructive text-sm font-bold flex items-center gap-3">
                              <ShieldCheck className="w-5 h-5" />
                              <span>You have reached the limit of 5 API keys. Please delete an existing key to create a new one.</span>
                            </div>
                          )}

                          <DialogFooter className="mt-8 pt-6 border-t border-border/50">
                            <Button
                              className="w-full rounded-2xl h-12 font-bold"
                              disabled={isCreating || keys.length >= 5}
                              onClick={handleCreateKey}
                            >
                              {isCreating ? (
                                <div className="flex items-center gap-2">
                                  <LoadingSpinner size="sm" className="text-current" />
                                  <span className="uppercase tracking-widest text-[10px]">Generating...</span>
                                </div>
                              ) : keys.length >= 5 ? (
                                "Limit Reached"
                              ) : (
                                "Generate Key"
                              )}
                            </Button>
                          </DialogFooter>
                        </div>
                      )}
                    </DialogContent>
                  </Dialog>
                </div>

                {/* Keys list scrollable */}
                <div className="flex-1 overflow-hidden min-h-0 flex flex-col border border-border/40 rounded-2xl bg-muted/5">
                  {isLoading ? (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-4">
                      <LoadingSpinner className="text-primary/40" />
                      <p className="text-[10px] text-muted-foreground font-black uppercase tracking-[0.2em] opacity-60 animate-pulse">Synchronizing Tokens...</p>
                    </div>
                  ) : keys.length === 0 ? (
                    <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center p-8">
                      <div className="p-4 rounded-full bg-muted/20 text-muted-foreground border border-border/30">
                        <Key className="w-8 h-8 opacity-40" />
                      </div>
                      <div className="max-w-xs">
                        <p className="font-bold text-base mb-1">No API keys found</p>
                        <p className="text-xs text-muted-foreground">Generate your first key to start interacting with the security backends.</p>
                      </div>
                    </div>
                  ) : (
                    <ScrollArea className="flex-1">
                      <div className="p-4 space-y-3">
                        {keys.map((key) => (
                          <div
                            key={key.id}
                            className="group relative p-4 rounded-2xl bg-card border border-border/40 hover:border-primary/30 transition-all duration-300 flex items-center justify-between gap-4"
                          >
                            <div className="flex items-start gap-4 flex-1 min-w-0">
                              <div className="mt-1 p-2 rounded-xl bg-primary/10 text-primary border border-primary/20 shrink-0">
                                <ShieldCheck className="w-5 h-5" />
                              </div>
                              <div className="space-y-1 min-w-0 flex-1">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <h4 className="font-bold text-sm truncate max-w-[200px]">{key.name}</h4>
                                  <Badge variant="outline" className="rounded-md bg-primary/5 border-primary/20 text-[8px] font-black uppercase tracking-widest px-1.5 py-0">
                                    {key.backend}
                                  </Badge>
                                </div>
                                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                                  <span className="font-mono text-xs opacity-60">ID: {key.id.substring(0, 10)}...</span>
                                  <span className="flex items-center gap-1">
                                    <Clock className="w-3 h-3" />
                                    {new Date(key.created_at).toLocaleDateString()}
                                  </span>
                                  <span className="flex items-center gap-1">
                                    <Calendar className="w-3 h-3" />
                                    {(() => {
                                      if (key.expires_at === "Never") return "Never";
                                      const exp = new Date(key.expires_at);
                                      if (isNaN(exp.getTime())) return "Never";
                                      const now = new Date();
                                      const diffMs = exp.getTime() - now.getTime();
                                      if (diffMs <= 0) return "Expired";
                                      const diffYears = exp.getFullYear() - now.getFullYear();
                                      if (diffYears > 50) return "Never";
                                      const seconds = Math.floor(diffMs / 1000);
                                      const minutes = Math.floor(seconds / 60);
                                      const hours = Math.floor(minutes / 60);
                                      const days = Math.floor(hours / 24);
                                      if (days >= 365) return `${Math.floor(days / 365)}y`;
                                      if (days >= 30) return `${Math.floor(days / 30)}mo`;
                                      if (days >= 1) return `${days}d`;
                                      if (hours >= 1) return `${hours}h`;
                                      if (minutes >= 1) return `${minutes}m`;
                                      return `${seconds}s`;
                                    })()}
                                  </span>
                                </div>
                              </div>
                            </div>

                            <Button
                              variant="outline"
                              size="sm"
                              className="h-9 px-3 rounded-lg border-border/50 hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30 shrink-0 text-xs font-bold"
                              onClick={() => setKeyToDelete(key.id)}
                            >
                              <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                              Delete
                            </Button>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col gap-6 animate-in fade-in duration-300">
                <div className="rounded-2xl border border-border/40 bg-card p-6 flex items-center gap-5">
                  <div className="relative w-16 h-16 rounded-full overflow-hidden border border-border/50 shrink-0 bg-muted flex items-center justify-center">
                    {user?.picture ? (
                      <img src={user.picture} alt={user.name} className="w-full h-full object-cover" />
                    ) : (
                      <User className="w-8 h-8 text-muted-foreground/45" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-lg font-black tracking-tight text-foreground truncate">{user?.name}</h3>
                    <p className="text-xs text-muted-foreground truncate uppercase tracking-widest font-bold opacity-60 flex items-center gap-1.5 mt-0.5">
                      <Mail className="w-3.5 h-3.5 text-muted-foreground/50" />
                      {user?.email}
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-xs font-black uppercase tracking-wider text-muted-foreground px-1">
                    Identity Metadata
                  </h4>
                  <div className="rounded-2xl border border-border/40 bg-muted/5 divide-y divide-border/40 overflow-hidden text-xs">
                    <div className="p-4 flex items-center justify-between gap-4">
                      <span className="font-bold text-muted-foreground">User ID</span>
                      <span className="font-mono bg-zinc-950 px-3 py-1.5 rounded-lg border border-white/5 text-muted-foreground max-w-[300px] truncate select-all">{user?.sub}</span>
                    </div>
                    <div className="p-4 flex items-center justify-between gap-4">
                      <span className="font-bold text-muted-foreground">Nickname</span>
                      <span className="font-semibold text-foreground">{user?.nickname || "N/A"}</span>
                    </div>
                    <div className="p-4 flex items-center justify-between gap-4">
                      <span className="font-bold text-muted-foreground">Email Verified</span>
                      <span className={cn("font-bold uppercase tracking-wider text-[10px] px-2.5 py-0.5 rounded-full border", user?.email_verified ? "bg-emerald-500/5 text-emerald-500 border-emerald-500/20" : "bg-destructive/5 text-destructive border-destructive/20")}>
                        {user?.email_verified ? "Verified" : "Unverified"}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!keyToDelete} onOpenChange={(open) => !open && setKeyToDelete(null)}>
        <AlertDialogContent className="rounded-[2.5rem] border-border/50 p-8 bg-background/95 backdrop-blur-xl">
          <AlertDialogHeader>
            <div className="w-16 h-16 rounded-[2rem] bg-destructive/10 text-destructive flex items-center justify-center mb-6 mx-auto sm:mx-0">
              <Trash2 className="w-8 h-8" />
            </div>
            <AlertDialogTitle className="text-2xl font-black">Revoke API Key?</AlertDialogTitle>
            <AlertDialogDescription className="text-base text-muted-foreground">
              This action is permanent. Any systems or scripts currently using this key will immediately lose access to the security backends.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="mt-8 gap-3">
            <AlertDialogCancel className="rounded-2xl h-12 font-bold border-border/50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="rounded-2xl h-12 font-bold bg-destructive hover:bg-destructive/90 text-destructive-foreground shadow-lg shadow-destructive/20"
              onClick={() => {
                if (keyToDelete) {
                  handleRevokeKey(keyToDelete);
                  setKeyToDelete(null);
                }
              }}
            >
              Confirm Revocation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
