"use client";

import { useEffect, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface Matter {
  id: number;
  title: string;
  matter_type: string;
  jurisdiction: string;
  language: string;
}

export function MatterBar({
  matterId,
  onSelect,
}: {
  matterId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const [matters, setMatters] = useState<Matter[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/matters`);
      if (res.ok) setMatters((await res.json()) as Matter[]);
    } catch {
      /* backend down — chat shows the toast path */
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const create = async () => {
    const name = title.trim();
    if (!name) return;
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/matters`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: name }),
      });
      if (!res.ok) throw new Error(await res.text());
      const created = (await res.json()) as Matter;
      setMatters((m) => [...m, created]);
      onSelect(created.id);
      setTitle("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "creation failed");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="matter-select" className="text-sm font-semibold">
        القضية / Dossier
      </label>
      <select
        id="matter-select"
        value={matterId ?? ""}
        onChange={(e) =>
          onSelect(e.target.value === "" ? null : Number(e.target.value))
        }
        className="rounded-md border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
      >
        <option value="">—</option>
        {matters.map((m) => (
          <option key={m.id} value={m.id}>
            #{m.id} {m.title}
          </option>
        ))}
      </select>
      <input
        aria-label="new matter title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="قضية جديدة… / Nouveau dossier…"
        className="rounded-md border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
      />
      <button
        type="button"
        onClick={() => void create()}
        className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700"
      >
        + إنشاء / Créer
      </button>
      {error && (
        <span role="alert" className="text-sm text-red-500">
          {error}
        </span>
      )}
    </div>
  );
}
