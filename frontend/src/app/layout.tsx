import type { Metadata } from "next";
import "./globals.css";

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
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
