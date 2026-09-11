import type { NextConfig } from "next";

// The browser only ever talks to this origin; Next.js forwards API calls to FastAPI.
// Same origin keeps the SameSite=lax session cookie working without CORS.
const apiOrigin = (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // Draft generation and prompt tests can wait out Groq rate limits; the 30s default cuts them off.
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/:path*` },
      // Google redirects back to /auth/callback on this origin, so the session cookie lands here.
      { source: "/auth/:path*", destination: `${apiOrigin}/auth/:path*` },
    ];
  },
};

export default nextConfig;
