import { ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";

type DetailSection = {
  id: "health" | "account" | "ledger" | "diagnostics";
  title: string;
  description: string;
  summary: string;
  status: string;
  warning?: boolean;
  content: ReactNode;
};

type Props = {
  health: Omit<DetailSection, "id" | "title">;
  account: Omit<DetailSection, "id" | "title">;
  ledger: Omit<DetailSection, "id" | "title">;
  diagnostics: Omit<DetailSection, "id" | "title">;
};

export default function LiveOperationsDetails({ health, account, ledger, diagnostics }: Props) {
  const [openSections, setOpenSections] = useState<Set<string>>(() => new Set());
  const sections: DetailSection[] = [
    { id: "health", title: "交易健康", ...health },
    { id: "account", title: "账户与持仓", ...account },
    { id: "ledger", title: "订单账本", ...ledger },
    { id: "diagnostics", title: "详细诊断", ...diagnostics },
  ];

  function toggle(id: string) {
    setOpenSections((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div data-testid="live-operations-details" className="min-w-0 border-t border-zinc-800">
      {sections.map((section) => {
        const open = openSections.has(section.id);
        const panelId = `live-operations-${section.id}`;
        return (
          <section key={section.id} className="min-w-0 border-b border-zinc-800">
            <button
              type="button"
              aria-expanded={open}
              aria-controls={panelId}
              onClick={() => toggle(section.id)}
              className="flex w-full min-w-0 items-center gap-3 px-1 py-3 text-left sm:px-2"
            >
              <ChevronDown className={`h-4 w-4 shrink-0 text-zinc-600 transition-transform ${open ? "rotate-180" : ""}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h2 className="text-sm font-semibold text-zinc-100">{section.title}</h2>
                  <span className="text-xs text-zinc-500">{section.summary}</span>
                </div>
                <p className="mt-0.5 break-words text-xs text-zinc-600">{section.description}</p>
              </div>
              <span className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-medium ${section.warning ? "border-amber-500/30 bg-amber-500/10 text-amber-300" : "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"}`}>
                {section.status}
              </span>
            </button>
            {open && <div id={panelId} className="min-w-0 border-t border-zinc-900 py-3">{section.content}</div>}
          </section>
        );
      })}
    </div>
  );
}
