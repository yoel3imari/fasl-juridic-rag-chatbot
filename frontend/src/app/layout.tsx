import type { Metadata } from "next";
import { Noto_Sans_Arabic } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const notoArabic = Noto_Sans_Arabic({
  subsets: ["arabic", "latin"],
  display: "swap",
  variable: "--font-arabic",
});

export const metadata: Metadata = {
  title: "Moroccan Legal RAG",
  description: "Citizen-first Moroccan legal assistant (Arabic / French)",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ar" dir="rtl" suppressHydrationWarning>
      <body
        className={`${notoArabic.variable} font-arabic bg-background text-foreground antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
