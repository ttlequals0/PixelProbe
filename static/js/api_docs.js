        window.onload = function() {
            const ui = SwaggerUIBundle({
                url: "/api/openapi.yaml",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout",
                // Try-it-out runs with the session cookie, so cookie writes need the CSRF header.
                requestInterceptor: (req) => {
                    const method = (req.method || 'GET').toUpperCase();
                    const sameOrigin = new URL(req.url, window.location.origin).origin === window.location.origin;
                    const token = document.querySelector('meta[name="csrf-token"]')?.content;
                    if (sameOrigin && token && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
                        req.headers['X-CSRFToken'] = token;
                    }
                    return req;
                }
            });
            window.ui = ui;
        };
