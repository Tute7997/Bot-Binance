import "./globals.css";

export const metadata = {
  title: "Dashboard Bot Trading",
  description: "Panel en vivo del bot de trading en Binance Testnet",
};

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        {children}
      </body>
    </html>
  );
}
