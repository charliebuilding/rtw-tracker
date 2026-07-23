// Basic-Auth gate. Runs at Netlify's edge before any page is served.
// Password comes from the SITE_PASSWORD env var if set, else falls back to the default below.
export default async (request, context) => {
  const PASSWORD = Netlify.env.get("SITE_PASSWORD") || "FNL26";
  const header = request.headers.get("authorization") || "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try { decoded = atob(header.slice(6)); } catch (_) {}
    const pass = decoded.slice(decoded.indexOf(":") + 1); // accept any username; check password only
    if (pass === PASSWORD) return context.next();          // authenticated → serve the real page
  }
  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="RTW Tracker", charset="UTF-8"' },
  });
};
