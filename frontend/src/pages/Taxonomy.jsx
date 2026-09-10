import { useState } from 'react';
import { useTaxonomies, useTaxonomyHcvs } from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';

// Read-only: taxonomies arrive by import command (backend/taxonomy/
// views.py's TaxonomyViewSet is a ReadOnlyModelViewSet), never edited here.
export default function Taxonomy() {
  const taxonomiesQuery = useTaxonomies();
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  // `Taxonomy.active()` (backend/taxonomy/models.py) guarantees at most one
  // active row at the database level (a partial unique index), so `.find`
  // is safe here - there is never more than one to find.
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active);

  const [selectedId, setSelectedId] = useState(null);
  const effectiveId = selectedId ?? activeTaxonomy?.id ?? taxonomies[0]?.id ?? null;

  const hcvsQuery = useTaxonomyHcvs(effectiveId);
  const hcvs = [...(hcvsQuery.data?.results ?? [])].sort((a, b) => a.order - b.order);

  if (taxonomiesQuery.isLoading) {
    return <p className="text-sm text-gray-500">Loading taxonomy…</p>;
  }

  if (taxonomiesQuery.isError) {
    return <ErrorBanner error={taxonomiesQuery.error} />;
  }

  if (taxonomies.length === 0) {
    return (
      <EmptyState
        title="No taxonomy imported"
        message="HCV-21 arrives by import command; there is nothing to show until one is loaded."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Taxonomy</h1>
        <p className="mt-1 text-sm text-gray-500">
          The human-centric vulnerability taxonomy, read-only. The active version is what a new
          experiment guards variant A against and labels variant B with.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <div className="max-w-xs flex-1">
          <label htmlFor="taxonomy-version" className="block text-sm font-medium text-gray-700">
            Version
          </label>
          <select
            id="taxonomy-version"
            value={effectiveId ?? ''}
            onChange={(event) => setSelectedId(Number(event.target.value))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            {taxonomies.map((taxonomy) => (
              <option key={taxonomy.id} value={taxonomy.id}>
                {taxonomy.vocabulary_id} v{taxonomy.version}
                {taxonomy.is_active ? ' (active)' : ''}
              </option>
            ))}
          </select>
        </div>
        {effectiveId === activeTaxonomy?.id && activeTaxonomy && (
          <span className="mt-5 inline-flex items-center rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-700">
            Active taxonomy
          </span>
        )}
      </div>

      {hcvsQuery.isLoading && <p className="text-sm text-gray-500">Loading HCVs…</p>}
      {hcvsQuery.isError && <ErrorBanner error={hcvsQuery.error} />}

      {hcvsQuery.isSuccess && (
        <ul className="space-y-3">
          {hcvs.map((hcv) => (
            <li key={hcv.id} className="rounded-lg border border-gray-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-gray-900">
                {hcv.hcv_id} — {hcv.name}
              </h3>
              {hcv.definition && <p className="mt-1 text-sm text-gray-600">{hcv.definition}</p>}
              {hcv.condition_classes?.length > 0 && (
                <p className="mt-2 text-xs text-gray-400">{hcv.condition_classes.join(', ')}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
