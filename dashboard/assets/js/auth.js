// Auth0 Logic
let auth0Client = null;

const authConfig = {
    domain: "dev-axwc0ui527kw0c5d.us.auth0.com",
    clientId: "sqVN3z2Er7YxXYK4FxbpOaYOqL2ju22D",
    audience: "https://code-inspector-api"
};

const initAuth0 = async () => {
    try {
        const clientCreator = window.createAuth0Client || (window.auth0 && window.auth0.createAuth0Client);

        if (!clientCreator) {
            throw new Error("Auth0 Security SDK failed to load. Please check your internet connection.");
        }

        auth0Client = await clientCreator({
            domain: authConfig.domain,
            clientId: authConfig.clientId,
            authorizationParams: {
                audience: authConfig.audience,
                scope: 'openid profile email',
                redirect_uri: window.location.origin + window.location.pathname
            }
        });

        if (window.location.search.includes("code=") && window.location.search.includes("state=")) {
            await auth0Client.handleRedirectCallback();
            window.history.replaceState({}, document.title, window.location.pathname);
        }

        updateUI();

        const loginBtn = document.getElementById("login-btn");
        if (loginBtn) {
            loginBtn.disabled = false;
            loginBtn.style.opacity = "1";
            loginBtn.style.cursor = "pointer";
            loginBtn.textContent = "Sign In with Auth0";
        }

    } catch (err) {
        console.error("Auth0 Init Error:", err);
        const errorBox = document.getElementById("init-error");
        if (errorBox) {
            errorBox.classList.remove("hidden");
            errorBox.innerHTML = `<strong>Initialization Failed:</strong> ${err.message}<br><br>Ensure you are accessing via <u>http://localhost</u> or <u>ngrok</u>.`;
        }

        const loginBtn = document.getElementById("login-btn");
        if (loginBtn) loginBtn.textContent = "Security Error";
    }
};

const updateUI = async () => {
    const isAuthenticated = await auth0Client.isAuthenticated();

    if (isAuthenticated) {
        const user = await auth0Client.getUser();
        const token = await auth0Client.getTokenSilently();

        document.getElementById("public-view").classList.add("hidden");
        document.getElementById("dashboard-view").classList.remove("hidden");
        document.getElementById("user-nav").classList.remove("hidden");
        document.getElementById("dashboard-view").classList.add("flex");

        document.getElementById("user-avatar").src = user.picture;

        await fetchAPIKeys();
        document.cookie = `inspector_auth=${token}; SameSite=Strict; Path=/`;

    } else {
        document.getElementById("public-view").classList.remove("hidden");
        document.getElementById("dashboard-view").classList.add("hidden");
        document.getElementById("user-nav").classList.add("hidden");
        document.getElementById("dashboard-view").classList.remove("flex");
    }
};

const login = async () => {
    await auth0Client.loginWithRedirect();
};

const logout = () => {
    document.cookie = "inspector_auth=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    document.cookie = "execution_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    document.cookie = "auth_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";

    auth0Client.logout({
        logoutParams: {
            returnTo: window.location.origin + window.location.pathname
        }
    });
};
