"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Strategy } from "@/lib/api";
import { SourceBadge } from "@/components/ActionBadge";

export default function StrategiesPage() {
  const [items, setItems] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    try {
      const { strategies } = await api.listStrategies();
      setItems(strategies);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { reload(); }, []);

  async function onDelete(id: string) {
    if (!confirm(`確定要刪除策略 "${id}"？`)) return;
    try {
      await api.deleteStrategy(id);
      reload();
    } catch (e: any) {
      alert("刪除失敗: " + e.message);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">策略庫</h1>
          <p className="text-sm text-muted mt-1">所有可用的選股策略 — 可手動建立、AI 生成、或執行</p>
        </div>
        <div className="flex gap-2">
          <Link href="/strategies/new" className="btn-ghost">＋ 手動新增</Link>
          <Link href="/strategies/ai" className="btn-primary">✨ AI 生策略</Link>
        </div>
      </div>

      {loading && <div className="card text-muted">載入中…</div>}
      {error && <div className="card border-err/40 text-err">錯誤：{error}</div>}

      {!loading && !error && items.length === 0 && (
        <div className="card text-muted">尚無策略，請新增或讓 AI 生一份。</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {items.map((s) => (
          <div key={s.id} className="card">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-medium">{s.name}</h3>
                  <SourceBadge source={s.source} />
                </div>
                <div className="text-xs text-muted font-mono">{s.id}</div>
              </div>
            </div>
            {s.description && (
              <p className="text-sm text-muted mt-3 leading-relaxed line-clamp-3">{s.description}</p>
            )}
            <div className="grid grid-cols-3 gap-2 mt-4 text-xs">
              <Field label="EPS≥" value={s.params?.eps_threshold} />
              <Field label="ROE≥" value={s.params?.roe_threshold} />
              <Field label="總分≥" value={s.params?.min_total_score_for_buy} />
              <Field label="停利" value={pct(s.params?.target_return)} />
              <Field label="停損" value={pct(s.params?.stop_loss)} />
              <Field label="持有日" value={s.params?.hold_days} />
            </div>
            <div className="flex gap-2 mt-4 pt-4 border-t border-line">
              <Link href={`/strategies/${s.id}`} className="btn-ghost flex-1">詳情 / 跑一次</Link>
              <button
                onClick={() => onDelete(s.id)}
                className="btn-danger"
                disabled={s.source === "default"}
                title={s.source === "default" ? "預設策略不可刪除" : "刪除"}
              >
                🗑
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <div className="text-muted">{label}</div>
      <div className="font-mono">{value ?? "—"}</div>
    </div>
  );
}
function pct(v?: number) {
  if (typeof v !== "number") return "—";
  return `${(v * 100).toFixed(1)}%`;
}
