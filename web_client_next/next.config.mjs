/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000';
    return [
      {
        source: '/pdfs/:path*',
        destination: `${backendUrl}/pdfs/:path*`
      },
      {
        source: '/ws',
        destination: `${backendUrl}/ws`
      }
    ]
  }
};

export default nextConfig;
