import { useState } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { Key, Plus, Copy, CheckCircle2, Loader2, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import DOMPurify from "dompurify";
import { getApiBaseUrl } from "@/lib/apiConfig";

const API_BASE_URL = getApiBaseUrl();

const TTL_UNITS = [
  { value: "minutes", label: "Minutes", multiplier: 1 / 60 },
  { value: "hours",   label: "Hours",   multiplier: 1 },
  { value: "days",    label: "Days",    multiplier: 24 },
  { value: "months",  label: "Months",  multiplier: 24 * 30 },
  { value: "years",   label: "Years",   multiplier: 24 * 365 },
  { value: "never",   label: "Never expire", multiplier: 0 },
];

interface InlineApiKeyPanelProps {
  backendId?: string;
}

export function InlineApiKeyPanel({ backendId = "Z1_SANDBOX" }: InlineApiKeyPanelProps) {
  const { user, getAccessTokenSilently } = useAuth0();
  const [open, setOpen] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [ttlValue, setTtlValue] = useState("30");
  const [ttlUnit, setTtlUnit] = useState("days");
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [keyVisible, setKeyVisible] = useState(false);
  const [copied, setCopied] = useState(false);

  const reset = () => {
    setNewKey(null);
    setKeyName("");
    setTtlValue("30");
    setTtlUnit("days");
    setCopied(false);
    setKeyVisible(false);
  };

  const computeTtlHours = (): number => {
    if (ttlUnit === "never") return -1;
    const val = parseInt(ttlValue);
    if (isNaN(val) || val <= 0) return -1;
    const unit = TTL_UNITS.find(u => u.value === ttlUnit)!;
    return val * unit.multiplier;
  };

  const computeExpiresAt = (): string => {
    if (ttlUnit === "never") return "Never";
    const hours = computeTtlHours();
    if (hours <= 0) return "Never";
    return new Date(Date.now() + hours * 3600000).toISOString();
  };

  const handleCreate = async () => {
    const sanitizedName = DOMPurify.sanitize(keyName).trim();
    if (!sanitizedName) { toast.error("Please enter a key name"); return; }
    if (ttlUnit !== "never") {
      const val = parseInt(ttlValue);
      if (isNaN(val) || val <= 0) { toast.error("TTL must be a positive number"); return; }
    }

    try {
      setIsCreating(true);
      let token = "";
      try { token = await getAccessTokenSilently(); } catch { /* local dev */ }

      const ttl_hours = computeTtlHours();

      if (!token) {
        const mockId = "key_" + Math.random().toString(36).substr(2, 9);
        const mockVal = "z1_" + Math.random().toString(36).substr(2, 24);
        const stored = localStorage.getItem("local_mock_api_keys");
        const list = stored ? JSON.parse(stored) : [];
        list.push({
          id: mockId, name: sanitizedName, backend: backendId,
          created_at: new Date().toISOString(),
          expires_at: computeExpiresAt(),
          is_revoked: false,
        });
        localStorage.setItem("local_mock_api_keys", JSON.stringify(list));
        localStorage.setItem(`bound_key_${mockId}`, mockVal);
        setNewKey(mockVal);
        setKeyName("");
        toast.success("API key created (local dev)");
        window.dispatchEvent(new Event("api-keys-changed"));
        return;
      }

      const res = await fetch(`${API_BASE_URL}/v1/api-keys`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: sanitizedName, backend: backendId, ttl_hours, user_email: user?.email }),
      });

      if (!res.ok) { const err = await res.json(); throw new Error(err.detail || "Failed to create key"); }

      const data = await res.json();
      localStorage.setItem(`bound_key_${data.api_key_id}`, data.api_key);
      setNewKey(data.api_key);
      setKeyName("");
      toast.success("API key created!");
      window.dispatchEvent(new Event("api-keys-changed"));
    } catch (e: any) {
      toast.error(e.message || "Failed to create key");
    } finally {
      setIsCreating(false);
    }
  };

  const handleCopy = () => {
    if (!newKey) return;
    navigator.clipboard.writeText(newKey);
    setCopied(true);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  const showTtlInput = ttlUnit !== "never";

  return (
    <Popover open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-9 rounded-xl border border-border/50 font-bold text-xs flex items-center gap-1.5 text-foreground hover:text-foreground hover:bg-secondary/30"
          title="API Key Settings"
        >
          <Key className="w-3.5 h-3.5" />
          API Keys
        </Button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-76 p-4 rounded-xl border border-border/50 shadow-xl bg-background">
        <div className="flex flex-col gap-3">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Key className="w-3.5 h-3.5 text-violet-500" />
              <span className="text-xs font-black uppercase tracking-wider">Create API Key</span>
            </div>
            <span className="text-[9px] text-muted-foreground/60 font-semibold uppercase bg-muted/40 px-1.5 py-0.5 rounded">
              {backendId}
            </span>
          </div>

          {newKey ? (
            /* ── Success state ── */
            <div className="flex flex-col gap-2.5 p-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5">
              <div className="flex items-center gap-1.5 text-emerald-500">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span className="text-[10px] font-black uppercase tracking-wider">Key Created Successfully</span>
              </div>
              <p className="text-[10px] text-muted-foreground leading-snug">
                Copy and save this key — it won't be shown again.
              </p>
              <div className="flex items-center gap-1.5">
                <code className="flex-1 font-mono text-[9px] bg-muted/40 border border-border/40 rounded px-2 py-1.5 truncate text-foreground/80">
                  {keyVisible ? newKey : "••••••••••••••••••"}
                </code>
                <button type="button" onClick={() => setKeyVisible(v => !v)} className="text-muted-foreground hover:text-foreground p-1 rounded">
                  {keyVisible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                </button>
                <button type="button" onClick={handleCopy} className={cn("p-1 rounded transition-colors", copied ? "text-emerald-500" : "text-muted-foreground hover:text-foreground")}>
                  {copied ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
              <Button size="sm" variant="outline" className="h-7 text-[10px] font-bold" onClick={() => { reset(); setOpen(false); }}>
                Done
              </Button>
            </div>
          ) : (
            /* ── Create form ── */
            <div className="flex flex-col gap-2.5">
              {/* Key name */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Key Name</label>
                <Input
                  placeholder="e.g. my-scanner-key"
                  value={keyName}
                  onChange={e => setKeyName(e.target.value)}
                  className="h-8 text-xs rounded-lg border-border/50 bg-background"
                  onKeyDown={e => e.key === "Enter" && handleCreate()}
                />
              </div>

              {/* TTL */}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Expiration</label>
                <div className="flex gap-2">
                  {showTtlInput && (
                    <Input
                      type="number"
                      min="1"
                      value={ttlValue}
                      onChange={e => setTtlValue(e.target.value)}
                      className="h-8 text-xs rounded-lg border-border/50 bg-background w-20 shrink-0"
                    />
                  )}
                  <Select value={ttlUnit} onValueChange={setTtlUnit}>
                    <SelectTrigger className="h-8 text-xs rounded-lg border-border/50 bg-background flex-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TTL_UNITS.map(u => (
                        <SelectItem key={u.value} value={u.value} className="text-xs">
                          {u.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button
                onClick={handleCreate}
                disabled={isCreating || !keyName.trim()}
                className="h-8 w-full rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-bold text-[11px] flex items-center gap-1.5 uppercase tracking-wider mt-0.5"
              >
                {isCreating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                {isCreating ? "Creating…" : "Create Key"}
              </Button>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
