/** @type {import('next').NextConfig} */
const nextConfig = {
  // Local smoke tests and the in-app browser use the explicit loopback host.
  // Allow its development assets so React hydration and HMR are not blocked.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
