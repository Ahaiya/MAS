import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";
import { CHART_COLORS } from "@/lib/constants";
import type { DimensionVM } from "@/lib/dataTransforms";

interface Props {
  dimensions: DimensionVM[];
}

export function DimensionRadarChart({ dimensions }: Props) {
  const data = dimensions.map((d) => ({
    subject:   d.shortLabel,
    MAS:       d.masScore,
    人类均分:  d.humanMean ?? 0,
    fullMark:  6,
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <RadarChart data={data} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
        <PolarGrid />
        <PolarAngleAxis dataKey="subject" tick={{ fontSize: 11 }} />
        <Radar
          name="MAS"
          dataKey="MAS"
          stroke={CHART_COLORS.mas}
          fill={CHART_COLORS.mas}
          fillOpacity={0.25}
          strokeWidth={2}
        />
        <Radar
          name="人类均分"
          dataKey="人类均分"
          stroke={CHART_COLORS.human}
          fill={CHART_COLORS.human}
          fillOpacity={0.15}
          strokeWidth={2}
          strokeDasharray="5 3"
        />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
        <Tooltip
          formatter={(value: unknown, name: unknown) => [`${value ?? 0} / 6`, String(name ?? "")]}
          contentStyle={{ fontSize: 12 }}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
