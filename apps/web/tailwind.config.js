/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    // If you have a src folder
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
    // ui packages
    "../../packages/ui/src/**/*.{js,ts,jsx,tsx,mdx}"
  ],
  theme: {
    extend: {
      colors: {
        re: {
          canvas: "var(--re-bg-canvas)",
          surface: "var(--re-bg-surface)",
          subtle: "var(--re-bg-subtle)",
          border: "var(--re-border-default)",
          "border-strong": "var(--re-border-strong)",
          text: "var(--re-text-primary)",
          secondary: "var(--re-text-secondary)",
          muted: "var(--re-text-muted)",
          brand: "var(--re-brand-700)",
          action: "var(--re-action-600)",
          info: "var(--re-info-600)",
          success: "var(--re-success-600)",
          warning: "var(--re-warning-600)",
          danger: "var(--re-danger-600)",
          neutral: "var(--re-neutral-600)",
        },
      },
      borderRadius: {
        "re-control": "var(--re-radius-control)",
        "re-panel": "var(--re-radius-panel)",
      },
      boxShadow: {
        "re-panel": "var(--re-shadow-panel)",
      },
      fontFamily: {
        sans: [
          "Pretendard Variable",
          "Noto Sans KR",
          "Inter",
          "system-ui",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
}
