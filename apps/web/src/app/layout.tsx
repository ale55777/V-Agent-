import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EchoAI",
  description: "A private, real-time AI voice assistant that runs entirely on your own computer.",
};

export const viewport: Viewport = { themeColor: "#07080f", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
