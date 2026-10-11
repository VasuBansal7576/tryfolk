export default {
  poweredByHeader: false,
  experimental: { cpus: 2 },
  async headers() {
    return [{source: '/:path*', headers: [
      {key:'Referrer-Policy', value:'no-referrer'},
      {key:'X-Content-Type-Options', value:'nosniff'},
      {key:'Cache-Control', value:'no-store'},
    ]}];
  },
};
