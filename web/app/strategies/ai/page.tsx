"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, Strategy } from "@/lib/api";
import StrategyForm from "@/components/StrategyForm";

const EXAMPLES = [
  "我想做短線動能，5-10 天持有，偏好飆股，停損嚴一點 -5%、停利 +15%。基本面門檻可以放寬。",
  "想長期存股，偏價值型，EPS、ROE 都要高，技術只是參考，回測權重高，持有 60 天以上。",
  "高股息存股策略，重視基本面穩定，停損鬆一點，最多接受跌 -10%，停利目標 +20%。",
  "保守型，所有條件都要嚴格，總分一定要 80 以上才考慮 BUY，停損 -3%。",
];

export default function AIStrategyPage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Strategy | null>(null);

  async function generate() {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setDraft(null);
    try {
      const s = await api.generateAI(prompt.trim(), name.trim() || undefined);
      setDraft(s);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <Link href="/strategies" className="text-sm text-muted hover:text-text">← 回策略庫</Link>

      <div>
        <h1 className="text-2xl font-semibold">✨ AI 生策略</h1>
        <p className="text-sm text-muted mt-1">
          用一段自然語言描述你想要的選股風格，Gemini 會自動生出對應的參數，你可以再微調後存進策略庫。
        </p>
      </div>

      <div className="card space-y-4">
        <div>
          <label className="label">想要的策略風格</label>
          <textarea
            className="input min-h-[120px]"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例：我想做短線動能，5-10 天持有，停損 -5%、停利 +15%"
          />
        </div>
        <div>
          <label className="label">策略名稱（可選）</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="留空 AI 會自己取" />
        </div>
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((e, i) => (
            <button key={i} onClick={() => setPrompt(e)}
              className="text-xs text-muted hover:text-text border border-line rounded-full px-3 py-1.5 hover:bg-panel2">
              範例 {i + 1}
            </button>
          ))}
        </div>
        <button onClick={generate} disabled={loading || !prompt.trim()} className="btn-primary">
          {loading ? "生成中…" : "✨ 用 Gemini 生策略"}
        </button>
        {error && <div className="text-sm text-err">錯誤：{error}</div>}
      </div>

      {draft && (
        <div className="space-y-4">
          <div className="card border-purple-500/30">
            <div className="flex items-center gap-2 mb-2">
              <span className="badge-ai">AI 草稿</span>
              <h2 className="font-medium">{draft.name}</h2>
            </div>
            {draft.description && <p className="text-sm text-muted">{draft.description}</p>}
            <p className="text-xs text-muted mt-3">
              下方表單已填入 AI 給的參數，你可以調整後再儲存。
            </p>
          </div>
          <StrategyForm
            initial={draft}
            saveLabel="儲存到策略庫"
            onSaved={(s) => router.push(`/strategies/${s.id}`)}
          />
        </div>
      )}
    </div>
  );
}
