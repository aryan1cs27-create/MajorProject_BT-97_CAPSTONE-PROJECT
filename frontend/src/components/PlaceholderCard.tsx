interface PlaceholderCardProps {
  title: string;
  description: string;
}

/** Honest placeholder: shown on pages whose features are not implemented yet. */
export default function PlaceholderCard({ title, description }: PlaceholderCardProps) {
  return (
    <section className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
      <span className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-brand-50 text-brand-600">
        !
      </span>
      <h2 className="text-base font-semibold text-navy-900">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-navy-500">{description}</p>
      <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-400">
        Not implemented yet
      </p>
    </section>
  );
}
