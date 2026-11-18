import { AlertOctagon, AlertTriangle, CheckCircle2, Eye } from "lucide-react";
import { Badge } from "@/components/ui";
import { humanize } from "@/lib/format";

const ICON = { critical: AlertOctagon, at_risk: AlertTriangle, watch: Eye, healthy: CheckCircle2 } as const;

/** Status shown as icon + label + colour, never colour alone. */
export function RiskBadge({ classification, score }: { classification: string; score?: number }) {
  const Icon = ICON[classification as keyof typeof ICON] ?? Eye;
  return (
    <Badge tone={classification} className="gap-1">
      <Icon className="h-3 w-3" />
      {humanize(classification)}
      {score !== undefined && <span className="tabular-nums opacity-80">· {score}</span>}
    </Badge>
  );
}
