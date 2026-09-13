import { useState } from 'react';
import { useAuth } from '../contexts/auth-context';
import {
  useActivateTaxonomy,
  useCreateHcv,
  useCreateTaxonomyVersion,
  useDeleteHcv,
  useTaxonomies,
  useTaxonomyHcvs,
  useUpdateHcv,
} from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';

const emptyHcvForm = { hcv_id: '', name: '', definition: '', condition_classes: '', order: '' };

// "1.1" -> "1.2": bump the trailing integer segment. Anything that doesn't
// end in a plain integer (e.g. "draft") falls back to "<version>-2" rather
// than guessing at a versioning scheme it can't parse.
function suggestNextVersion(version) {
  const parts = String(version).split('.');
  const last = parts[parts.length - 1];
  if (/^\d+$/.test(last)) {
    parts[parts.length - 1] = String(Number(last) + 1);
    return parts.join('.');
  }
  return `${version}-2`;
}

function parseConditionClasses(text) {
  return text
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

// Add/Edit share the same fields - only the submit handler and initial
// values differ between the two modals that render this.
function HcvForm({ form, onChange, onSubmit, onCancel, pending, error, submitLabel }) {
  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <div>
        <label htmlFor="hcv-id" className="block text-sm font-medium text-gray-700">
          HCV id
        </label>
        <input
          id="hcv-id"
          value={form.hcv_id}
          onChange={(event) => onChange({ ...form, hcv_id: event.target.value })}
          required
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
        />
      </div>
      <div>
        <label htmlFor="hcv-name" className="block text-sm font-medium text-gray-700">
          Name
        </label>
        <input
          id="hcv-name"
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
          required
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
        />
      </div>
      <div>
        <label htmlFor="hcv-definition" className="block text-sm font-medium text-gray-700">
          Definition
        </label>
        <textarea
          id="hcv-definition"
          value={form.definition}
          onChange={(event) => onChange({ ...form, definition: event.target.value })}
          rows={3}
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
        />
      </div>
      <div>
        <label htmlFor="hcv-condition-classes" className="block text-sm font-medium text-gray-700">
          Condition classes (comma-separated)
        </label>
        <input
          id="hcv-condition-classes"
          value={form.condition_classes}
          onChange={(event) => onChange({ ...form, condition_classes: event.target.value })}
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
        />
      </div>
      <div>
        <label htmlFor="hcv-order" className="block text-sm font-medium text-gray-700">
          Order
        </label>
        <input
          id="hcv-order"
          type="number"
          value={form.order}
          onChange={(event) => onChange({ ...form, order: event.target.value })}
          required
          className="mt-1 block w-32 rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
        />
      </div>

      {error && <ErrorBanner error={error} />}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {pending ? 'Saving…' : submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function Taxonomy() {
  const { isStaff } = useAuth();

  const taxonomiesQuery = useTaxonomies();
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  // `Taxonomy.active()` (backend/taxonomy/models.py) guarantees at most one
  // active row at the database level (a partial unique index), so `.find`
  // is safe here - there is never more than one to find.
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active);

  const [selectedId, setSelectedId] = useState(null);
  const effectiveId = selectedId ?? activeTaxonomy?.id ?? taxonomies[0]?.id ?? null;
  const selectedTaxonomy = taxonomies.find((taxonomy) => taxonomy.id === effectiveId) ?? null;

  const hcvsQuery = useTaxonomyHcvs(effectiveId);
  const hcvs = [...(hcvsQuery.data?.results ?? [])].sort((a, b) => a.order - b.order);

  const activateTaxonomy = useActivateTaxonomy();
  const createVersion = useCreateTaxonomyVersion();
  const createHcv = useCreateHcv();
  const updateHcv = useUpdateHcv();
  const deleteHcv = useDeleteHcv();

  const [hcvModalMode, setHcvModalMode] = useState(null); // null | 'add' | 'edit'
  const [editingHcv, setEditingHcv] = useState(null);
  const [hcvForm, setHcvForm] = useState(emptyHcvForm);
  const [deletingHcv, setDeletingHcv] = useState(null);
  const [versionModalOpen, setVersionModalOpen] = useState(false);
  const [versionValue, setVersionValue] = useState('');

  function openAddModal() {
    const maxOrder = hcvs.reduce((max, hcv) => Math.max(max, hcv.order), 0);
    setEditingHcv(null);
    setHcvForm({ ...emptyHcvForm, order: String(maxOrder + 1) });
    setHcvModalMode('add');
  }

  function openEditModal(hcv) {
    setEditingHcv(hcv);
    setHcvForm({
      hcv_id: hcv.hcv_id,
      name: hcv.name,
      definition: hcv.definition ?? '',
      condition_classes: (hcv.condition_classes ?? []).join(', '),
      order: String(hcv.order),
    });
    setHcvModalMode('edit');
  }

  function closeHcvModal() {
    setHcvModalMode(null);
    setEditingHcv(null);
  }

  async function handleHcvSubmit(event) {
    event.preventDefault();
    const payload = {
      hcv_id: hcvForm.hcv_id.trim(),
      name: hcvForm.name.trim(),
      definition: hcvForm.definition,
      condition_classes: parseConditionClasses(hcvForm.condition_classes),
      order: Number(hcvForm.order),
    };
    try {
      if (hcvModalMode === 'edit') {
        await updateHcv.mutateAsync({ id: editingHcv.id, ...payload });
      } else {
        await createHcv.mutateAsync({ taxonomyId: effectiveId, ...payload });
      }
      closeHcvModal();
    } catch {
      // Surfaced below via createHcv.error/updateHcv.error inside the modal.
    }
  }

  async function handleDeleteConfirmed() {
    try {
      await deleteHcv.mutateAsync(deletingHcv.id);
      setDeletingHcv(null);
    } catch {
      // Dialog stays open so the API's own refusal is visible.
    }
  }

  async function handleActivate() {
    try {
      await activateTaxonomy.mutateAsync(effectiveId);
    } catch {
      // Surfaced below via activateTaxonomy.error.
    }
  }

  function openVersionModal() {
    setVersionValue(suggestNextVersion(selectedTaxonomy.version));
    setVersionModalOpen(true);
  }

  async function handleCreateVersionSubmit(event) {
    event.preventDefault();
    try {
      const created = await createVersion.mutateAsync({ id: effectiveId, version: versionValue.trim() });
      setVersionModalOpen(false);
      setSelectedId(created.id);
    } catch {
      // Surfaced below via createVersion.error inside the modal.
    }
  }

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

  const isActive = !!activeTaxonomy && effectiveId === activeTaxonomy.id;
  const isLocked = !!selectedTaxonomy?.is_locked;
  const hcvMutation = hcvModalMode === 'edit' ? updateHcv : createHcv;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Taxonomy</h1>
        <p className="mt-1 text-sm text-gray-500">
          The closed list variant B is shown and phase 3 labels from - variant A never sees it, and
          its rows are also what variant A&apos;s leak check scans for.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
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
        {isActive && (
          <span className="mt-5 inline-flex items-center rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-700">
            Active taxonomy
          </span>
        )}
        {hcvsQuery.isSuccess && <span className="mt-5 text-sm text-gray-500">{hcvs.length} HCVs</span>}
      </div>

      {!isActive && isStaff && (
        <div className="space-y-1">
          <button
            type="button"
            onClick={handleActivate}
            disabled={activateTaxonomy.isPending}
            className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {activateTaxonomy.isPending ? 'Activating…' : 'Make active'}
          </button>
          <p className="text-xs text-gray-500">
            The active version is what new experiments and iterate runs use by default.
          </p>
          {activateTaxonomy.isError && <ErrorBanner error={activateTaxonomy.error} />}
        </div>
      )}

      {isStaff && isLocked && (
        <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm text-amber-800">{selectedTaxonomy.locked_reason}</p>
          <button
            type="button"
            onClick={openVersionModal}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Create new version
          </button>
        </div>
      )}

      {isStaff && !isLocked && (
        <button
          type="button"
          onClick={openAddModal}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Add HCV
        </button>
      )}

      {hcvsQuery.isLoading && <p className="text-sm text-gray-500">Loading HCVs…</p>}
      {hcvsQuery.isError && <ErrorBanner error={hcvsQuery.error} />}

      {hcvsQuery.isSuccess && (
        <ul className="space-y-3">
          {hcvs.map((hcv) => (
            <li key={hcv.id} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                <h3 className="text-sm font-semibold text-gray-900">
                  {hcv.hcv_id} — {hcv.name}
                </h3>
                {isStaff && !isLocked && (
                  <div className="flex shrink-0 gap-2">
                    <button
                      type="button"
                      onClick={() => openEditModal(hcv)}
                      className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => setDeletingHcv(hcv)}
                      className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
              {hcv.definition && <p className="mt-1 text-sm text-gray-600">{hcv.definition}</p>}
              {hcv.condition_classes?.length > 0 && (
                <p className="mt-2 text-xs text-gray-400">{hcv.condition_classes.join(', ')}</p>
              )}
              {deletingHcv?.id === hcv.id && (
                <div className="mt-3 space-y-2">
                  <ConfirmDialog
                    title={`Delete ${hcv.hcv_id}?`}
                    confirmLabel="Delete"
                    pending={deleteHcv.isPending}
                    onConfirm={handleDeleteConfirmed}
                    onCancel={() => setDeletingHcv(null)}
                  />
                  {deleteHcv.isError && <ErrorBanner error={deleteHcv.error} />}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {hcvModalMode && (
        <Modal title={hcvModalMode === 'edit' ? `Edit ${editingHcv.hcv_id}` : 'Add HCV'} onClose={closeHcvModal}>
          <HcvForm
            form={hcvForm}
            onChange={setHcvForm}
            onSubmit={handleHcvSubmit}
            onCancel={closeHcvModal}
            pending={hcvMutation.isPending}
            error={hcvMutation.isError ? hcvMutation.error : null}
            submitLabel={hcvModalMode === 'edit' ? 'Save' : 'Add'}
          />
        </Modal>
      )}

      {versionModalOpen && (
        <Modal title="Create new taxonomy version" onClose={() => setVersionModalOpen(false)}>
          <form onSubmit={handleCreateVersionSubmit} className="space-y-3">
            <div>
              <label htmlFor="new-version" className="block text-sm font-medium text-gray-700">
                Version
              </label>
              <input
                id="new-version"
                value={versionValue}
                onChange={(event) => setVersionValue(event.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
              />
            </div>
            {createVersion.isError && <ErrorBanner error={createVersion.error} />}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={createVersion.isPending}
                className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {createVersion.isPending ? 'Creating…' : 'Create version'}
              </button>
              <button
                type="button"
                onClick={() => setVersionModalOpen(false)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
