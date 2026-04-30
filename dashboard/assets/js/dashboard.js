// Main Dashboard Logic
let backendKeys = {};
let availableBackends = [];

const fetchBackends = async () => {
    try {
        const response = await fetch("/config/backends.json");
        if (response.ok) {
            availableBackends = await response.json();
            // Process backends to ensure they have all required fields
            availableBackends = availableBackends.map(b => ({
                ...b,
                baseUrl: b.baseUrl || `/api/${b.id.toLowerCase()}`,
                documentationUrl: b.documentationUrl || b.baseUrl || `/api/${b.id.toLowerCase()}/docs`
            }));
        } else {
            console.warn("Backends config not found, using empty list.");
            availableBackends = [];
        }
        renderBackends();
        populateBackendSelect();
    } catch (err) {
        console.error("Failed to fetch backends config:", err);
    }
};

const renderBackends = () => {
    const container = document.getElementById("backends-container");
    if (!container) return;

    if (availableBackends.length === 0) {
        container.innerHTML = '<div class="col-span-full py-12 text-center text-muted-foreground"><p>No backends configured.</p></div>';
        return;
    }

    container.innerHTML = availableBackends.map(backend => `
        <div class="aws-widget hover:border-primary/50 transition-colors group shadow-sm bg-card">
            <div class="aws-widget-header bg-card">
                <h3 class="aws-widget-title text-foreground text-base">
                    <div class="bg-${backend.color || 'indigo'}-500/10 text-${backend.color || 'indigo'}-400 p-1.5 rounded-md text-xl mr-2">${backend.icon === 'terminal' ? '⚡' : '🌐'}</div> 
                    ${backend.name}
                </h3>
            </div>
            <div class="aws-widget-body bg-muted/5 group-hover:bg-card transition-colors">
                <p class="text-sm text-foreground mb-4">${backend.description}</p>
                <div class="flex gap-2">
                    <button
                        class="flex-1 bg-accent/10 border border-accent/20 text-accent font-semibold py-2 px-3 rounded text-xs hover:bg-accent/20 transition-all flex items-center justify-center gap-1.5"
                        onclick="openScanner('${backend.id}', '${backend.baseUrl}')">
                        <span>🔍</span> Quick Scan
                    </button>
                    <button
                        class="flex-1 bg-muted border border-border text-foreground font-semibold py-2 px-3 rounded text-xs hover:bg-muted-foreground/10 transition-all"
                        onclick="bindAndNavigate('${backend.id}', '${backend.documentationUrl}')">
                        Go to docs
                    </button>
                </div>
            </div>
        </div>
    `).join('');
};

const populateBackendSelect = () => {
    const select = document.getElementById("key-backend");
    if (!select) return;
    select.innerHTML = availableBackends.map(b => `
        <option value="${b.id}">${b.name} (${b.documentationUrl})</option>
    `).join('');
};

const fetchAPIKeys = async () => {
    try {
        const list = document.getElementById("keys-list");
        if (!list) return;
        
        list.innerHTML = `<div class="p-8 text-center"><div class="spinner mx-auto border-border border-t-primary"></div></div>`;
        
        const token = await auth0Client.getTokenSilently();
        const response = await fetch("/v1/api-keys", {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        
        if (data.keys) {
            data.keys.forEach(key => {
                if (!key.is_revoked) {
                    const savedKey = localStorage.getItem(`bound_key_${key.id}`);
                    if (savedKey) {
                        backendKeys[key.backend] = savedKey;
                        // Default to Z1_SANDBOX for execution token cookie
                        if (key.backend === 'Z1_SANDBOX' && !getCookie('execution_token')) {
                            document.cookie = `execution_token=${savedKey}; SameSite=Strict; Path=/`;
                        }
                    }
                }
            });
        }
        
        renderKeys(data.keys);
    } catch (err) {
        console.error("Failed to fetch keys:", err);
        renderKeys([]);
    }
};

const renderKeys = (keys) => {
    const list = document.getElementById("keys-list");
    if (!list) return;
    
    const activeKeys = keys ? keys.filter(k => !k.is_revoked) : [];

    if (activeKeys.length === 0) {
        list.innerHTML = `
            <div class="px-6 py-12 text-center text-muted-foreground w-full col-span-full">
                <div class="text-4xl mb-4 opacity-50">🔑</div>
                <p class="text-sm font-medium">No active API keys found.<br>Create one above to enable backend integrations.</p>
            </div>`;
        return;
    }

    list.innerHTML = activeKeys.map(key => `
        <div class="grid grid-cols-12 gap-4 px-6 py-4 items-center hover:bg-muted/30 transition-colors group">
            <div class="col-span-12 md:col-span-5 flex-grow mb-2 md:mb-0">
                <div class="font-medium text-sm text-foreground mb-1">${key.name}</div>
                <div class="flex items-center gap-2">
                     <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-accent/10 text-accent border border-accent/20">${key.backend}</span>
                     <span class="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded border border-border">${key.id.substring(0, 8)}</span>
                </div>
            </div>
            <div class="col-span-12 md:col-span-4 text-xs font-mono text-muted-foreground">
                <div class="flex flex-col gap-1">
                    <span>Key: ${key.prefix}...</span>
                    <span class="text-[10px] text-muted-foreground/60 font-sans">Created: ${new Date(key.created_at).toLocaleDateString()}</span>
                </div>
            </div>
            <div class="col-span-12 md:col-span-3 text-right flex justify-end">
               <button class="opacity-100 md:opacity-0 group-hover:opacity-100 px-3 py-1.5 bg-destructive/10 text-destructive text-xs font-semibold rounded border border-destructive/20 hover:bg-destructive hover:text-destructive-foreground transition-all" onclick="deleteKey('${key.id}')">Revoke</button>
            </div>
        </div>
    `).join('');
};

let currentScannerBackend = null;
let currentScannerUrl = null;

const openScanner = (backend, url) => {
    currentScannerBackend = backend;
    currentScannerUrl = url;
    
    document.getElementById("scanner-target-label").textContent = backend + " :: SECURE CLUSTER";
    document.getElementById("scan-status-pill").innerHTML = "";
    document.getElementById("scan-response").innerHTML = `
        <div class="flex flex-col items-center justify-center h-full text-muted-foreground opacity-40">
             <span class="text-4xl mb-4">🧪</span>
             <p class="text-sm font-medium">Ready for code submission</p>
        </div>
    `;
    document.getElementById("scan-payload").value = "# Simple Code Example\ndef greet(name):\n    return f\"Hello, {name}!\"\n\nprint(greet(\"User\"))";
    
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("scanner-modal").classList.remove("hidden");
    document.getElementById("create-modal").classList.add("hidden");
    document.getElementById("reveal-modal").classList.add("hidden");
};

const detectLanguage = (code) => {
    const text = code.trim();
    // 1. JSON
    if (text.startsWith("{") || text.startsWith("[")) return "json";
    
    // 2. YAML
    if (text.startsWith("---") || /^(apiVersion|metadata|version|services|spec|kind|items):/m.test(text)) return "yaml";
    if (/^[a-z0-9_]+:\s/m.test(text) && !text.includes("def ") && !text.includes("function ")) return "yaml";

    // 3. Go
    if (/\bpackage\s+main\b/.test(text) || /\bfunc\s+main\b/.test(text)) return "go";

    // 4. Bash
    if (text.startsWith("#!") || /\b(sudo|apt-get|yum|printf|exec|export\s+[A-Z_]+=)/m.test(text)) return "sh";

    // 5. Javascript / Node.js
    if (/\b(const|let|var|function|console\.log|require\(|module\.exports|async\s+function|await\s)\b/.test(text)) return "js";

    // 6. Python
    if (/\b(def\s|class\s|import\s|from\s.*import|if\s+__name__\s+==)/m.test(text) || /\bprint\(/.test(text)) return "py";
    
    return "py"; 
};

const submitScan = async () => {
    const code = document.getElementById("scan-payload").value;
    const responseEl = document.getElementById("scan-response");
    const statusPill = document.getElementById("scan-status-pill");
    const runBtn = document.getElementById("run-scan-btn");
    
    if (!code.trim()) {
        showToast("Please provide some code to scan", "error");
        return;
    }

    const key = backendKeys[currentScannerBackend];
    if (!key) {
        showToast(`No API key found for ${currentScannerBackend}. Please create one first.`, "error");
        return;
    }

    const ext = detectLanguage(code);
    const filename = `input.${ext}`;

    try {
        runBtn.disabled = true;
        runBtn.innerHTML = '<div class="spinner w-4 h-4 border-2 border-t-white mr-2"></div> AUDITING...';
        statusPill.innerHTML = '<span class="bg-amber-500/10 text-amber-500 px-2 py-0.5 rounded text-[10px] border border-amber-500/20 animate-pulse font-bold">AUDITING</span>';
        
        responseEl.innerHTML = `
            <div class="flex flex-col gap-3 p-6 bg-slate-100/50 dark:bg-accent/5 border border-slate-200 dark:border-accent/20 rounded-xl shadow-sm">
                <div class="flex items-center gap-3">
                    <div class="spinner w-4 h-4"></div>
                    <span class="text-xs text-slate-700 dark:text-accent font-semibold tracking-tight">Provisioning sandbox and initializing ${ext.toUpperCase()} session...</span>
                </div>
                <div class="text-[10px] text-slate-500 dark:text-muted-foreground font-medium uppercase tracking-wider">Target: <span class="text-emerald-600 dark:text-emerald-400 font-mono">${filename}</span></div>
            </div>
        `;

        const response = await fetch(`${currentScannerUrl}/v1/scan-jobs`, {
            method: 'POST',
            headers: {
                'accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${key}`
            },
            body: JSON.stringify({
                files: { [filename]: code }
            })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || "Backend returned an error");

        statusPill.innerHTML = '<span class="bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded text-[10px] border border-emerald-400/20 font-bold tracking-tight">SUCCESS</span>';
        
        responseEl.innerHTML = `
<div class="flex flex-col gap-4 animate-[fadeIn_0.3s_ease-out]">
    <div class="p-4 bg-slate-100 dark:bg-muted/20 border border-slate-200 dark:border-border/30 rounded-lg flex justify-between items-center shadow-sm">
        <div class="flex flex-col gap-1">
             <span class="text-[10px] text-slate-400 dark:text-muted-foreground uppercase font-black opacity-50">Cluster Identity (Sandbox ID)</span>
             <code class="text-[11px] text-accent">${data.sandbox_id || 'N/A'}</code>
        </div>
        <div class="text-right">
             <span class="text-[10px] text-slate-400 dark:text-muted-foreground uppercase font-black opacity-50">Audit Timestamp</span>
             <div class="text-[11px] text-slate-600 dark:text-foreground font-medium">${new Date().toLocaleTimeString()}</div>
        </div>
    </div>

    <div class="grid grid-cols-1 gap-1">
        <div class="text-[10px] text-slate-400 dark:text-muted-foreground uppercase font-black opacity-50 mb-2">Audit Report (${data.report?._totals?.loc || 1} artifacts)</div>
        <pre class="bg-slate-900 dark:bg-[#0d1117] p-6 rounded-xl border border-slate-800 dark:border-border/30 overflow-auto text-emerald-400 shadow-xl max-h-[450px] leading-relaxed scrollbar-thin">${JSON.stringify(data.report || data, null, 4)}</pre>
    </div>
</div>`;
        showToast("Code audit completed!", "success");
    } catch (err) {
        statusPill.innerHTML = '<span class="bg-destructive/10 text-destructive px-2 py-0.5 rounded text-[10px] border border-destructive/20 font-bold">FAILED</span>';
        responseEl.innerHTML = `<div class="p-4 bg-destructive/5 border border-destructive/20 rounded-lg text-destructive text-xs"><strong>Audit Error:</strong> ${err.message}</div>`;
        showToast("Scan failed", "error");
    } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = 'Run Security Audit';
    }
};

const showCreateModal = () => {
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("create-modal").classList.remove("hidden");
    document.getElementById("reveal-modal").classList.add("hidden");
    document.getElementById("scanner-modal").classList.add("hidden");
};

const closeModals = () => {
    document.getElementById("modal-overlay").classList.add("hidden");
    document.getElementById("create-modal").classList.add("hidden");
    document.getElementById("reveal-modal").classList.add("hidden");
    document.getElementById("scanner-modal").classList.add("hidden");
    document.getElementById("key-name").value = "";
};

const submitCreateKey = async () => {
    const name = document.getElementById("key-name").value;
    const backend = document.getElementById("key-backend").value;
    const ttlValue = parseInt(document.getElementById("key-ttl-value").value) || 1;
    const ttlUnit = document.getElementById("key-ttl-unit").value;
    let finalTtlHours = 1;

    if (!name) {
        showToast("Please enter a Key Name", "error");
        return;
    }

    if (ttlUnit === "hours") finalTtlHours = ttlValue;
    else if (ttlUnit === "days") finalTtlHours = ttlValue * 24;
    else if (ttlUnit === "months") finalTtlHours = ttlValue * 24 * 30;
    else if (ttlUnit === "years") finalTtlHours = ttlValue * 24 * 365;
    else if (ttlUnit === "never") finalTtlHours = -1;

    try {
        const token = await auth0Client.getTokenSilently();
        const user = await auth0Client.getUser();
        const response = await fetch("/v1/api-keys", {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                name, backend, 
                ttl_hours: finalTtlHours,
                user_email: user.email 
            })
        });

        const data = await response.json();
        if (!response.ok) {
            let errorMsg = "Failed to generate key";
            if (data.detail) {
                errorMsg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            }
            throw new Error(errorMsg);
        }

        await fetchAPIKeys();
        localStorage.setItem(`bound_key_${data.api_key_id}`, data.api_key);
        backendKeys[backend] = data.api_key;
        document.cookie = `execution_token=${data.api_key}; SameSite=Strict; Path=/`;
        
        document.getElementById("create-modal").classList.add("hidden");
        document.getElementById("reveal-modal").classList.remove("hidden");
        document.getElementById("new-key-display").textContent = data.api_key;
        
        // Update docs link in reveal modal
        const selectedBackend = availableBackends.find(b => b.id === backend);
        if (selectedBackend) {
            document.getElementById("docs-link").href = selectedBackend.documentationUrl;
        }

        showToast("API Key generated successfully!", "success");
    } catch (err) {
        showToast("Creation Failed: " + err.message, "error");
    }
};

const deleteKey = async (jti) => {
    if (!confirm("Are you sure you want to revoke this key?")) return;
    try {
        const token = await auth0Client.getTokenSilently();
        await fetch(`/v1/api-keys/${jti}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        showToast("Key revoked successfully", "info");
        fetchAPIKeys();
    } catch (err) {
        showToast("Failed to revoke key: " + err.message, "error");
    }
};

const copyNewKey = () => {
    const token = document.getElementById("new-key-display").textContent;
    navigator.clipboard.writeText(token);
    showToast("Key copied to clipboard!", "success");
};

const bindAndNavigate = (backend, url) => {
    const key = backendKeys[backend];
    if (!key) {
        showToast(`No API Key found for ${backend}. Please create one in the 'APIs' tab first.`, 'error');
        showAPIPage();
        return;
    }
    document.cookie = `execution_token=${key}; SameSite=Strict; Path=/`;
    window.open(url, '_blank');
};

// Navigation
const setActiveNav = (id) => {
    const items = ['menu-apis', 'menu-applications'];
    items.forEach(item => {
        const el = document.getElementById(item);
        if (el) el.className = item === id 
            ? 'menu-item active flex items-center gap-3 px-3 py-2.5 rounded-md bg-accent/10 text-accent font-medium cursor-pointer mb-1'
            : 'menu-item flex items-center gap-3 px-3 py-2.5 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground font-medium cursor-pointer transition-colors mb-1';
    });
};

const showAPIPage = () => {
    document.getElementById('api-page').classList.remove('hidden');
    document.getElementById('applications-page').classList.add('hidden');
    setActiveNav('menu-apis');
    fetchAPIKeys();
};

const showApplicationsPage = () => {
    document.getElementById('applications-page').classList.remove('hidden');
    document.getElementById('api-page').classList.add('hidden');
    setActiveNav('menu-applications');
};

window.onload = () => {
    initThemeUI();
    fetchBackends();
    initAuth0();
};
