/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./web/index.html", "./web/app.js", "./web/license-ui.js"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "Inter", "sans-serif"],
        mono: ['"JetBrains Mono"', "Consolas", "monospace"],
        serif: ['"Cinzel"', "Georgia", "serif"],
      },
      colors: {
        brand: {
          50: "#f0f7ff",
          100: "#e0effe",
          200: "#bae0fd",
          300: "#7cc7fb",
          400: "#38a9f8",
          500: "#0e8de9",
          600: "#026fc7",
          700: "#0358a1",
          800: "#074b84",
          900: "#0c3f6e",
          950: "#082849",
        },
        glass: {
          surface: "rgba(255, 255, 255, 0.72)",
          surfaceHover: "rgba(255, 255, 255, 0.88)",
          border: "rgba(255, 255, 255, 0.85)",
          borderDark: "rgba(186, 224, 253, 0.45)",
        },
      },
      boxShadow: {
        glass: "0 12px 36px -8px rgba(2, 111, 199, 0.10), 0 4px 16px -4px rgba(2, 111, 199, 0.05)",
        "glass-lg": "0 24px 48px -12px rgba(2, 111, 199, 0.16), 0 8px 24px -6px rgba(2, 111, 199, 0.08)",
        "glow-sky": "0 0 25px -3px rgba(56, 169, 248, 0.35)",
      },
    },
  },
  plugins: [],
};
