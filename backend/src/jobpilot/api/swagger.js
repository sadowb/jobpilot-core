// Session credentials stay in this page's memory and are sent only to the local API.
async function renderDocumentation() {
  const response = await fetch('/api/v1/session', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Open /docs in your JobPilot browser session.');
  const session = await response.json();
  window.SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    displayRequestDuration: true,
    defaultModelsExpandDepth: 0,
    validatorUrl: null,
    persistAuthorization: false,
    requestInterceptor(request) {
      const url = new URL(request.url, window.location.origin);
      if (url.origin === window.location.origin) {
        request.credentials = 'same-origin';
        if (!['GET', 'HEAD'].includes(String(request.method || 'GET').toUpperCase())) {
          request.headers ||= {};
          request.headers['X-CSRF-Token'] = session.csrf;
        }
      }
      return request;
    },
  });
}
renderDocumentation().catch((error) => {
  document.getElementById('swagger-ui').textContent = error.message;
});
