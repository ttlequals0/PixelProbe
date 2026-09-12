(function installCsrfFetchGuard() {
    const unsafeMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
    const originalFetch = window.fetch.bind(window);

    window.fetch = (input, init = {}) => {
        const request = input instanceof Request ? input : null;
        const method = (init.method || request?.method || 'GET').toUpperCase();
        const url = new URL(request?.url || input, window.location.origin);
        if (!unsafeMethods.has(method) || url.origin !== window.location.origin) {
            return originalFetch(input, init);
        }

        const token = document.querySelector('meta[name="csrf-token"]')?.content;
        if (!token) return originalFetch(input, init);
        const headers = new Headers(request?.headers || undefined);
        new Headers(init.headers || undefined).forEach((value, key) => headers.set(key, value));
        headers.set('X-CSRFToken', token);
        return originalFetch(input, { ...init, headers });
    };
})();
