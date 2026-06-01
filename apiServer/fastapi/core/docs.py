from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


def render_swagger_ui(openapi_url: str, title: str):
    """
    Manually renders Swagger UI HTML with a raw JS requestInterceptor
    to enable automatic cookie forwarding (withCredentials).
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <title>{title}</title>
    </head>
    <body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        const ui = SwaggerUIBundle({{
            url: '{openapi_url}',
            dom_id: '#swagger-ui',
            presets: [
                SwaggerUIBundle.presets.apis,
                SwaggerUIBundle.SwaggerUIStandalonePreset
            ],
            layout: "BaseLayout",
            deepLinking: true,
            displayOperationId: true,
            persistAuthorization: true,
            requestInterceptor: (req) => {{
                req.credentials = 'include';
                return req;
            }}
        }});

        // AUTO-AUTHORIZATION: Read the developer key from the session cookie
        const getCookie = (name) => {{
            const value = `; ${{document.cookie}}`;
            const parts = value.split(`; ${{name}}=`);
            if (parts.length === 2) return parts.pop().split(';').shift();
        }};

        // Robust Authorization Injector
        const autoAuthorize = () => {{
            const token = getCookie('execution_token') || getCookie('inspector_auth');
            if (token && ui && ui.authActions) {{
                const formattedToken = token.startsWith('Bearer ') ? token : `Bearer ${{token}}`;

                // Clear any old auth and apply the new one
                ui.authActions.authorize({{
                    "BearerAuth": {{
                        name: "BearerAuth",
                        schema: {{
                            type: "apiKey",
                            in: "header",
                            name: "Authorization"
                        }},
                        value: formattedToken
                    }}
                }});
                console.log("[Zero-Touch] Successfully bound Developer Key to Swagger session.");
            }} else {{
                console.warn("[Zero-Touch] Waiting for UI or Cookie... Retrying in 1s");
                setTimeout(autoAuthorize, 1000);
            }}
        }};

        // Initial trigger
        setTimeout(autoAuthorize, 1000);
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


def register_docs_routes(app: FastAPI):
    """Registers the custom Swagger UI and OpenAPI JSON routes on the app."""

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        return render_swagger_ui(app.openapi_url, app.title + " - Docs")

    @app.get("/openapi.json", include_in_schema=False)
    async def custom_openapi_json():
        """
        Patches the global OpenAPI spec to define the Cookies as the security scheme.
        """
        spec = app.openapi()
        spec.setdefault("components", {})
        spec["components"].setdefault("securitySchemes", {})
        spec["components"]["securitySchemes"]["CookieAuth"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "inspector_auth",
        }
        spec["security"] = [{"CookieAuth": []}]
        return JSONResponse(content=spec)
