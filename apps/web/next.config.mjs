/** @type {import('next').NextConfig} */
const nextConfig = {
  // A static export needs no Node server at runtime, which frees 150+ MB for the AI models.
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  transpilePackages: ["@echoai/shared-types", "@echoai/api-client", "@echoai/agent-config"],
  devIndicators: false,
};

export default nextConfig;
