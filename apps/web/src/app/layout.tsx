import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Jarvis",
  description: "A private, real-time AI voice assistant that runs entirely on your own computer.",
};

export const viewport: Viewport = { themeColor: "#07080f", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Sets window.__ECHOAI_API__ to the backend's current address. scripts/share.ps1 rewrites
            it on the hosted copy each time the tunnel starts, so one fixed link keeps working. */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="/backend.js" />
      </head>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
