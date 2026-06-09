import type { NextConfig } from 'next'

// AGmind patch: same-origin reverse-proxy to OUR AgentOS (agent-agno). The browser-side Agent UI
// calls `/os-api/*` on its own origin; Next.js proxies it server-side to the AgentOS runtime — so
// there is no CORS, no per-install domain baked into the bundle, and no separate AgentOS exposure.
const upstream = process.env.AGENT_OS_UPSTREAM || 'http://agent-agno:8800'

const nextConfig: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [{ source: '/os-api/:path*', destination: `${upstream}/:path*` }]
  }
}

export default nextConfig
