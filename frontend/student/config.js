// AI Animal Lab — student site configuration.
//
// API_BASE is prepended to every API call (API_BASE + "/api/..."):
//
//   ""            -> SAME ORIGIN (the default). Use this when the backend serves
//                    this folder itself: local dev (http://localhost:8000) or
//                    when someone opens the tunnel URL directly.
//
//   "https://..." -> CROSS ORIGIN. REQUIRED on GitHub Pages: github.io serves
//                    the static files but the API lives behind the Cloudflare
//                    tunnel, so set API_BASE to the tunnel URL with NO trailing
//                    slash, e.g. "https://animal-lab.example.trycloudflare.com".
//
// A "?api=<url>" query parameter overrides this value at runtime (and the
// choice is remembered in localStorage), which is handy for pointing a phone
// at a test backend without editing files.
window.ANIMAL_LAB_CONFIG = { API_BASE: "" };
