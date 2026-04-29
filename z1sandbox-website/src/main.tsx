import { createRoot } from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import App from "./App.tsx";
import "./index.css";

const domain = import.meta.env.VITE_AUTH0_DOMAIN || "dev-axwc0ui527kw0c5d.us.auth0.com";
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID || "sqVN3z2Er7YxXYK4FxbpOaYOqL2ju22D";
const audience = import.meta.env.VITE_AUTH0_AUDIENCE || "https://code-inspector-api";

createRoot(document.getElementById("root")!).render(
  <Auth0Provider
    domain={domain}
    clientId={clientId}
    authorizationParams={{
      redirect_uri: window.location.origin,
      audience: audience,
      scope: "openid profile email"
    }}
    onRedirectCallback={() => {
      window.location.href = window.location.origin + "/dashboard";
    }}
  >
    <App />
  </Auth0Provider>
);
