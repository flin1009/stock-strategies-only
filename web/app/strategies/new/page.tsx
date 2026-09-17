"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import StrategyForm from "@/components/StrategyForm";

export default function NewStrategyPage() {
  const router = useRouter();
  return (
    <div className="space-y-6">
      <Link href="/strategies" className="text-sm text-muted hover:text-text">← 回策略庫</Link>
      <div>
        <h1 className="text-2xl font-semibold">手動建立策略</h1>
        <p className="text-sm text-muted mt-1">所有欄位都會用預設值填好，調你想動的就好。</p>
      </div>
      <StrategyForm onSaved={(s) => router.push(`/strategies/${s.id}`)} />
    </div>
  );
}
