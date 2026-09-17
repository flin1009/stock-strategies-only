import "./globals.css";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Stock Strategies — 策略庫",
  description: "台股每日選股策略庫 + AI 生策略",
};

const navItems = [
  { href: "/", label: "Dashboard" },
  { href: "/strategies", label: "策略庫" },
  { href: "/strategies/new", label: "新建策略" },
  { href: "/strategies/ai", label: "AI 生策略" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-TW">
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="border-b border-line">
            <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-6">
              <Link href="/" className="font-semibold tracking-wide">
                📈 <span className="text-text">Stock Strategies</span>
              </Link>
              <nav className="flex items-center gap-1 ml-2">
                {navItems.map((n) => (
                  <Link
                    key={n.href}
                    href={n.href}
                    className="px-3 py-1.5 rounded-md text-sm text-muted hover:text-text hover:bg-panel"
                  >
                    {n.label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>
          <main className="flex-1">
            <div className="max-w-6xl mx-auto px-6 py-8">{children}</div>
          </main>
          <footer className="border-t border-line text-xs text-muted">
            <div className="max-w-6xl mx-auto px-6 py-4">
              本工具僅供研究與紀錄之用，不構成投資建議。
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
