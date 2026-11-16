interface EmptyStateProps {
  title: string;
  description: string;
}

/** Shown when the backend returned zero records. */
export default function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <p className="text-sm font-medium text-navy-900">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-navy-500">{description}</p>
    </div>
  );
}
