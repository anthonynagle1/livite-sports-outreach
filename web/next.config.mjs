/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',      // Static HTML export — served by Flask
  trailingSlash: true,   // /hub → /hub/index.html
  images: {
    unoptimized: true,   // Required for static export
  },
};

export default nextConfig;
