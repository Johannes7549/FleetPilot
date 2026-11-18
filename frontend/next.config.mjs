/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://localhost:8000";
const nextConfig = {
  output: "standalone",
  // Browser talks to /api on the same origin; Next proxies to FastAPI (no CORS, no exposed backend URL).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
