import { useEffect, useState } from "react";
import { Github, Tag, Bug, Sparkles, BookOpen, Wrench, ChevronDown, ChevronUp, ExternalLink, RefreshCw } from "lucide-react";

// ─── Config ──────────────────────────────────────────────────────────────────
const GITHUB_REPO  = "01cloud/01-Sandbox";
const RAW_URL      = `https://raw.githubusercontent.com/${GITHUB_REPO}/main/CHANGELOG.md`;
const RELEASES_URL = `https://github.com/${GITHUB_REPO}/releases`;

// ─── Types ────────────────────────────────────────────────────────────────────
interface ChangeSection {
  type: string;
  items: { text: string; commitUrl?: string; commitSha?: string }[];
}

interface Release {
  version: string;
  date: string;
  compareUrl: string;
  sections: ChangeSection[];
}

// ─── Emoji / Icon map ─────────────────────────────────────────────────────────
const SECTION_META: Record<
  string,
  { icon: React.ElementType; color: string; label: string }
> = {
  "new features":  { icon: Sparkles,  color: "text-violet-500 dark:text-violet-400", label: "New Features"   },
  "features":      { icon: Sparkles,  color: "text-violet-500 dark:text-violet-400", label: "Features"       },
  "bug fixes":     { icon: Bug,        color: "text-rose-500   dark:text-rose-400",   label: "Bug Fixes"      },
  "documentation": { icon: BookOpen,   color: "text-sky-500    dark:text-sky-400",    label: "Documentation"  },
  "miscellaneous": { icon: Wrench,     color: "text-amber-500  dark:text-amber-400",  label: "Miscellaneous"  },
  "breaking changes":{ icon: Wrench,  color: "text-red-600    dark:text-red-400",    label: "Breaking Changes"},
};

function sectionMeta(raw: string) {
  const key = raw.toLowerCase().trim().replace(/^[^\w]+/, "");
  for (const [pattern, meta] of Object.entries(SECTION_META)) {
    if (key.includes(pattern)) return meta;
  }
  return { icon: Tag, color: "text-muted-foreground", label: raw };
}

// ─── Parser ───────────────────────────────────────────────────────────────────
function parseChangelog(md: string): Release[] {
  const releases: Release[] = [];
  const blocks = md.split(/(?=^## \[)/m).filter(Boolean);

  for (const block of blocks) {
    const headerMatch = block.match(
      /^## \[([^\]]+)\]\(([^)]+)\)\s+\((\d{4}-\d{2}-\d{2})\)/
    );
    if (!headerMatch) continue;

    const [, version, compareUrl, date] = headerMatch;
    const sections: ChangeSection[] = [];
    let currentSection: ChangeSection | null = null;

    for (const line of block.split("\n")) {
      const secMatch = line.match(/^### (.+)/);
      if (secMatch) {
        currentSection = { type: secMatch[1].trim(), items: [] };
        sections.push(currentSection);
        continue;
      }
      const itemMatch = line.match(/^\*\s+(.+)/);
      if (itemMatch && currentSection) {
        const raw = itemMatch[1];
        const linkMatch = raw.match(/^(.*?)\s+\(\[([a-f0-9]+)\]\(([^)]+)\)\)\s*$/);
        if (linkMatch) {
          currentSection.items.push({
            text: linkMatch[1].replace(/^\*\*[^*]+\*\*:\s*/, ""),
            commitSha: linkMatch[2],
            commitUrl: linkMatch[3],
          });
        } else {
          currentSection.items.push({ text: raw.replace(/^\*\*[^*]+\*\*:\s*/, "") });
        }
      }
    }

    const filtered = sections
      .map((s) => ({
        ...s,
        items: s.items.filter(
          (i) =>
            !i.text.includes("[skip ci]") &&
            !i.text.startsWith("bump versions") &&
            !i.text.startsWith("generate RELEASE.md") &&
            i.text.trim() !== ""
        ),
      }))
      .filter((s) => s.items.length > 0);

    releases.push({ version, date, compareUrl, sections: filtered });
  }

  return releases;
}

// ─── Release Card ─────────────────────────────────────────────────────────────
function ReleaseCard({
  release,
  index,
  isLatest,
}: {
  release: Release;
  index: number;
  isLatest: boolean;
}) {
  const [expanded, setExpanded] = useState(isLatest || index < 3);
  const totalItems = release.sections.reduce((a, s) => a + s.items.length, 0);

  const dotColors = [
    "from-violet-500 to-indigo-500",
    "from-sky-500 to-cyan-500",
    "from-emerald-500 to-teal-500",
    "from-rose-500 to-pink-500",
    "from-amber-500 to-orange-500",
  ];
  const dotColor = dotColors[index % dotColors.length];

  return (
    <div className="relative pl-10 pb-12 group">
      <div className="absolute left-[15px] top-0 bottom-0 w-px bg-gradient-to-b from-border via-border/40 to-transparent" />

      <div
        className={`absolute left-0 top-1.5 w-[30px] h-[30px] rounded-full bg-gradient-to-br ${dotColor} flex items-center justify-center shadow-lg ring-4 ring-background transition-transform group-hover:scale-110`}
      >
        <Tag className="w-3.5 h-3.5 text-white" />
      </div>

      <div className="ml-4 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-sm shadow-sm hover:shadow-md hover:border-border transition-all duration-300 overflow-hidden">
        <button
          className="w-full text-left px-6 py-4 flex items-start justify-between gap-4"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
        >
          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {isLatest && (
                <span className="text-[10px] font-black uppercase tracking-widest px-2.5 py-0.5 rounded-full bg-gradient-to-r from-violet-500 to-indigo-500 text-white shadow-sm">
                  Latest
                </span>
              )}
              <span className="font-display font-black text-lg tracking-tight text-foreground">
                v{release.version}
              </span>
              <a
                href={release.compareUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1 text-[10px] font-bold text-muted-foreground/60 hover:text-accent transition-colors uppercase tracking-wider"
              >
                <ExternalLink className="w-3 h-3" />
                Compare
              </a>
            </div>
            <span className="text-xs text-muted-foreground font-medium">
              {new Date(release.date).toLocaleDateString("en-US", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </span>
          </div>

          <div className="flex items-center gap-3 shrink-0 pt-0.5">
            <span className="text-[11px] font-bold text-muted-foreground/60 hidden sm:block">
              {totalItems} change{totalItems !== 1 ? "s" : ""}
            </span>
            {expanded ? (
              <ChevronUp className="w-4 h-4 text-muted-foreground/60" />
            ) : (
              <ChevronDown className="w-4 h-4 text-muted-foreground/60" />
            )}
          </div>
        </button>

        {expanded && (
          <div className="px-6 pb-6 pt-2 border-t border-border/40 space-y-5">
            {release.sections.map((section, si) => {
              const meta = sectionMeta(section.type);
              const Icon = meta.icon;
              return (
                <div key={si}>
                  <div className={`flex items-center gap-2 mb-2.5 ${meta.color}`}>
                    <Icon className="w-3.5 h-3.5 shrink-0" />
                    <span className="text-[11px] font-black uppercase tracking-widest">
                      {meta.label}
                    </span>
                  </div>
                  <ul className="space-y-1.5">
                    {section.items.map((item, ii) => (
                      <li key={ii} className="flex items-start gap-2 text-sm text-muted-foreground">
                        <span className="mt-[7px] w-1 h-1 rounded-full bg-muted-foreground/40 shrink-0" />
                        <span className="flex-1 leading-relaxed">
                          {item.text}
                          {item.commitUrl && (
                            <a
                              href={item.commitUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1.5 font-mono text-[10px] text-muted-foreground/40 hover:text-accent transition-colors"
                            >
                              {item.commitSha?.slice(0, 7)}
                            </a>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function Changelog() {
  const [releases, setReleases] = useState<Release[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastFetched, setLastFetched] = useState<Date | null>(null);
  const [search, setSearch] = useState("");

  async function fetchChangelog() {
    setLoading(true);
    setError(null);
    try {
      // 1. Try local bundled changelog first (works offline, in private repos, and instant)
      let res = await fetch(`/CHANGELOG.md?t=${Date.now()}`);

      // 2. Fall back to GitHub raw URL if local not available
      if (!res.ok) {
        res = await fetch(`${RAW_URL}?t=${Date.now()}`);
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      setReleases(parseChangelog(text));
      setLastFetched(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load changelog");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchChangelog();
  }, []);

  const normalizedSearch = search.trim().replace(/^v/i, "");

  const filtered = releases.filter(
    (r) =>
      normalizedSearch === "" ||
      r.version.includes(normalizedSearch) ||
      r.sections.some((s) =>
        s.items.some((i) => i.text.toLowerCase().includes(normalizedSearch.toLowerCase()))
      )
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Hero */}
      <div className="relative overflow-hidden border-b border-border/40 bg-gradient-to-b from-secondary/30 to-background pt-28 pb-16">
        <div className="absolute top-0 left-1/4 w-[500px] h-[500px] rounded-full bg-violet-500/5 blur-[100px] pointer-events-none" />
        <div className="absolute top-0 right-1/4 w-[400px] h-[400px] rounded-full bg-sky-500/5 blur-[120px] pointer-events-none" />

        <div className="container mx-auto px-6 max-w-4xl relative z-10">
          <div className="flex flex-col items-center text-center gap-6">
            <a
              href={RELEASES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-border/60 bg-card/60 backdrop-blur-sm text-xs font-bold text-muted-foreground hover:text-foreground hover:border-border transition-all hover:shadow-sm"
            >
              <Github className="w-3.5 h-3.5" />
              {GITHUB_REPO}
              <ExternalLink className="w-3 h-3 opacity-50" />
            </a>

            <div>
              <h1 className="font-display font-black text-4xl sm:text-5xl lg:text-6xl tracking-tight text-foreground leading-none">
                Changelog
              </h1>
            </div>

            {!loading && !error && (
              <div className="flex items-center gap-6 text-sm">
                <div className="text-center">
                  <div className="font-black text-2xl text-foreground">{releases.length}</div>
                  <div className="text-[10px] uppercase tracking-widest text-muted-foreground/60 font-bold">
                    Releases
                  </div>
                </div>
                <div className="w-px h-8 bg-border/50" />
                <div className="text-center">
                  <div className="font-black text-2xl text-foreground">
                    {releases.reduce(
                      (a, r) => a + r.sections.reduce((b, s) => b + s.items.length, 0),
                      0
                    )}
                  </div>
                  <div className="text-[10px] uppercase tracking-widest text-muted-foreground/60 font-bold">
                    Changes
                  </div>
                </div>
                {releases[0] && (
                  <>
                    <div className="w-px h-8 bg-border/50" />
                    <div className="text-center">
                      <div className="font-black text-2xl text-foreground">
                        v{releases[0].version}
                      </div>
                      <div className="text-[10px] uppercase tracking-widest text-muted-foreground/60 font-bold">
                        Latest
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            <div className="flex items-center gap-3 w-full max-w-md">
              <div className="relative flex-1">
                <input
                  type="search"
                  placeholder="Search releases..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full pl-4 pr-4 py-2.5 rounded-xl border border-border/60 bg-card/60 backdrop-blur-sm text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-accent/30 transition-all"
                />
              </div>
              <button
                onClick={fetchChangelog}
                disabled={loading}
                className="p-2.5 rounded-xl border border-border/60 bg-card/60 backdrop-blur-sm text-muted-foreground hover:text-foreground hover:bg-card transition-all disabled:opacity-40"
                title="Refresh"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>

            {lastFetched && (
              <p className="text-[10px] text-muted-foreground/40 font-medium">
                Last synced {lastFetched.toLocaleTimeString()}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Timeline — fixed-height scrollable frame */}
      <div className="container mx-auto px-6 max-w-3xl py-10 pb-16">
        <div className="h-[68vh] min-h-[400px] overflow-y-auto pr-1 custom-scrollbar">
          <div className="pt-2 pb-6">

        {loading && (
          <div className="flex flex-col items-center gap-4 py-20 text-muted-foreground">
            <div className="w-8 h-8 rounded-full border-2 border-accent border-t-transparent animate-spin" />
            <span className="text-sm font-medium">Fetching changelog from GitHub…</span>
          </div>
        )}

        {error && (
          <div className="flex flex-col items-center gap-4 py-20 text-center">
            <div className="w-12 h-12 rounded-2xl bg-destructive/10 flex items-center justify-center">
              <Bug className="w-6 h-6 text-destructive" />
            </div>
            <div>
              <p className="font-bold text-foreground mb-1">Failed to load changelog</p>
              <p className="text-sm text-muted-foreground mb-4">{error}</p>
              <a
                href={`https://github.com/${GITHUB_REPO}/blob/main/CHANGELOG.md`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-bold text-accent hover:underline"
              >
                <Github className="w-4 h-4" />
                View on GitHub
              </a>
            </div>
            <button
              onClick={fetchChangelog}
              className="px-5 py-2.5 rounded-xl bg-foreground text-background text-sm font-bold hover:opacity-90 transition-opacity"
            >
              Try Again
            </button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="text-center py-20 text-muted-foreground">
            <p className="text-lg font-bold mb-2">No releases found</p>
            <p className="text-sm">Try a different search term.</p>
          </div>
        )}

        {!loading && !error && filtered.length > 0 && (
          <div>
            {filtered.map((release, i) => (
              <ReleaseCard
                key={release.version}
                release={release}
                index={i}
                isLatest={i === 0 && normalizedSearch === ""}
              />
            ))}

            <div className="relative pl-10 flex items-center gap-4">
              <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[30px] h-[30px] rounded-full border-2 border-dashed border-border/50 flex items-center justify-center bg-background">
                <div className="w-2 h-2 rounded-full bg-border/50" />
              </div>
              <p className="ml-4 text-xs text-muted-foreground/40 font-medium uppercase tracking-widest">
                Beginning of history
              </p>
            </div>
          </div>
        )}

          </div>
        </div>
      </div>

    </div>
  );
}
