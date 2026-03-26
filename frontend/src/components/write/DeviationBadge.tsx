interface Props {
  deviation: number | null;
}

export function DeviationBadge({ deviation }: Props) {
  if (deviation == null) return <span className="text-xs text-gray-400">—</span>;

  const abs = Math.abs(deviation);
  const sign = deviation > 0 ? "+" : "";
  const label = `${sign}${deviation.toFixed(1)}`;

  if (abs <= 0.5) {
    return (
      <span className="text-xs font-medium text-green-600 bg-green-50 px-1.5 py-0.5 rounded">
        {label}
      </span>
    );
  }
  if (abs <= 1.0) {
    return (
      <span className="text-xs font-medium text-yellow-700 bg-yellow-50 px-1.5 py-0.5 rounded">
        {label}
      </span>
    );
  }
  if (abs <= 1.5) {
    return (
      <span className="text-xs font-medium text-orange-600 bg-orange-50 px-1.5 py-0.5 rounded">
        {label}
      </span>
    );
  }
  return (
    <span className="text-xs font-semibold text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
      {label}
    </span>
  );
}
