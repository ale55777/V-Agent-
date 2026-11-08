import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#07080f", 900: "#0c0e1a", 800: "#141728", 700: "#1e2238", 600: "#2c3150" },
        accent: { DEFAULT: "#7c6cff", soft: "#a79dff" },
        listen: "#34d399",
        think: "#fbbf24",
        speak: "#60a5fa",
        halt: "#f87171",
      },
      fontFamily: { sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"] },
      keyframes: {
        rise: { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "none" } },
        spinslow: { to: { transform: "rotate(360deg)" } },
        fade: { from: { opacity: "0" }, to: { opacity: "1" } },
        pulsedot: { "0%, 100%": { opacity: "1" }, "50%": { opacity: ".35" } },
      },
      animation: {
        rise: "rise .25s ease-out",
        spinslow: "spinslow 3s linear infinite",
        fade: "fade .4s ease-out",
        pulsedot: "pulsedot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
