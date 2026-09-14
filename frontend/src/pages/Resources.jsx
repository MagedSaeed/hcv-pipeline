import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useCreateFewShot,
  useCreateResource,
  useDeleteFewShot,
  useDeleteResource,
  useFewShots,
  useResources,
  useUpdateFewShot,
  useUpdateResource,
} from '../api/queries';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';
import Modal from '../components/Modal';
import CollapsibleText from '../components/CollapsibleText';
import Corpus from './Corpus';

// corpus.models.ResourceKind.choices.
const KIND_OPTIONS = [
  { value: 'system_description', label: 'System description' },
  { value: 'scenarios', label: 'Scenarios' },
  { value: 'vocabulary', label: 'Vocabulary' },
  { value: 'taxonomy', label: 'Taxonomy' },
  { value: 'other', label: 'Other' },
];

const KIND_LABELS = Object.fromEntries(KIND_OPTIONS.map((option) => [option.value, option.label]));

// A taxonomy-kind resource is the single most consequential label on this
// screen: spec §5.3 refuses it for a variant-A run whether it is reached
// through the `resource()` template function or through a few-shot's
// input/output file, so a researcher needs to see at a glance which of
// their resources can never appear in an unbiased prompt - not discover it
// only when a run fails.
const TAXONOMY_WARNING =
  'This is a taxonomy-kind resource. It will be refused if it reaches a variant A prompt or few-shot - variant A is never shown the taxonomy.';

const PHASE_OPTIONS = [
  { value: '2', label: 'Phase 2 — generation' },
  { value: '3', label: 'Phase 3 — labelling' },
];

const TABS = [
  { value: 'corpus', label: 'Systems & scenarios' },
  { value: 'fewshots', label: 'Few-shot examples' },
  { value: 'resources', label: 'Text resources' },
];

function ResourceKindBadge({ kind }) {
  const isTaxonomy = kind === 'taxonomy';
  const style = isTaxonomy
    ? 'bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-400'
    : 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${style}`}>
      {KIND_LABELS[kind] ?? kind}
    </span>
  );
}

// -----------------------------------------------------------------------
// Text resources

const emptyResourceForm = { name: '', kind: 'other', description: '', mode: 'file', file: null, text: '' };

function AddResourceModal({ onClose }) {
  const createResource = useCreateResource();
  const [form, setForm] = useState(emptyResourceForm);

  async function handleSubmit(event) {
    event.preventDefault();
    const body = new FormData();
    body.append('name', form.name);
    body.append('kind', form.kind);
    if (form.description) body.append('description', form.description);
    if (form.mode === 'file') {
      if (form.file) body.append('file', form.file);
    } else {
      body.append('text', form.text);
    }
    try {
      await createResource.mutateAsync(body);
      onClose();
    } catch {
      // Surfaced below via createResource.isError/error.
    }
  }

  return (
    <Modal title="Add resource" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="resource-add-name" className="block text-sm font-medium text-gray-700">
            Name
          </label>
          <input
            id="resource-add-name"
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>
        <div>
          <label htmlFor="resource-add-kind" className="block text-sm font-medium text-gray-700">
            Kind
          </label>
          <select
            id="resource-add-kind"
            value={form.kind}
            onChange={(event) => setForm((current) => ({ ...current, kind: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="resource-add-description" className="block text-sm font-medium text-gray-700">
            Description (optional)
          </label>
          <input
            id="resource-add-description"
            value={form.description}
            onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-gray-700">Content</legend>
          <div className="flex gap-4 text-sm text-gray-600">
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="resource-add-mode"
                checked={form.mode === 'file'}
                onChange={() => setForm((current) => ({ ...current, mode: 'file' }))}
              />
              Upload file
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="resource-add-mode"
                checked={form.mode === 'text'}
                onChange={() => setForm((current) => ({ ...current, mode: 'text' }))}
              />
              Paste text
            </label>
          </div>
          {form.mode === 'file' ? (
            <input
              aria-label="File (.txt, .md, .json — UTF-8 text)"
              type="file"
              onChange={(event) => setForm((current) => ({ ...current, file: event.target.files?.[0] ?? null }))}
              className="block w-full text-sm text-gray-700"
            />
          ) : (
            <textarea
              aria-label="Text"
              value={form.text}
              onChange={(event) => setForm((current) => ({ ...current, text: event.target.value }))}
              rows={6}
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          )}
        </fieldset>

        {createResource.isError && <ErrorBanner error={createResource.error} />}

        <button
          type="submit"
          disabled={createResource.isPending || !form.name}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {createResource.isPending ? 'Uploading…' : 'Upload'}
        </button>
      </form>
    </Modal>
  );
}

function EditResourceModal({ resource, onClose }) {
  const updateResource = useUpdateResource();
  const [form, setForm] = useState({
    name: resource.name,
    kind: resource.kind,
    description: resource.description || '',
    text: resource.text || '',
  });

  async function handleSubmit(event) {
    event.preventDefault();
    try {
      await updateResource.mutateAsync({ id: resource.id, ...form });
      onClose();
    } catch {
      // Surfaced below via updateResource.isError/error.
    }
  }

  return (
    <Modal title={`Edit ${resource.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor={`resource-edit-name-${resource.id}`} className="block text-sm font-medium text-gray-700">
            Name
          </label>
          <input
            id={`resource-edit-name-${resource.id}`}
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>
        <div>
          <label htmlFor={`resource-edit-kind-${resource.id}`} className="block text-sm font-medium text-gray-700">
            Kind
          </label>
          <select
            id={`resource-edit-kind-${resource.id}`}
            value={form.kind}
            onChange={(event) => setForm((current) => ({ ...current, kind: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor={`resource-edit-description-${resource.id}`}
            className="block text-sm font-medium text-gray-700"
          >
            Description (optional)
          </label>
          <input
            id={`resource-edit-description-${resource.id}`}
            value={form.description}
            onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>
        <div>
          <label htmlFor={`resource-edit-text-${resource.id}`} className="block text-sm font-medium text-gray-700">
            Text
          </label>
          <textarea
            id={`resource-edit-text-${resource.id}`}
            value={form.text}
            onChange={(event) => setForm((current) => ({ ...current, text: event.target.value }))}
            rows={10}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>

        {updateResource.isError && <ErrorBanner error={updateResource.error} />}

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={updateResource.isPending}
            className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {updateResource.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResourceRow({ resource }) {
  const deleteResource = useDeleteResource();
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  async function handleDeleteConfirmed() {
    setDeleteError(null);
    try {
      await deleteResource.mutateAsync(resource.id);
      setConfirmingDelete(false);
    } catch (error) {
      setDeleteError(error);
    }
  }

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900">{resource.name}</p>
          {resource.description && <p className="text-xs text-gray-500">{resource.description}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ResourceKindBadge kind={resource.kind} />
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            disabled={deleteResource.isPending}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Delete
          </button>
        </div>
      </div>

      {resource.kind === 'taxonomy' && (
        <p role="note" className="mt-2 text-xs font-medium text-amber-800">
          {TAXONOMY_WARNING}
        </p>
      )}

      <div className="mt-2">
        <CollapsibleText text={resource.text} empty="(no extracted text)" />
      </div>

      {confirmingDelete && (
        <div className="mt-2">
          <ConfirmDialog
            title={`Delete "${resource.name}"?`}
            pending={deleteResource.isPending}
            onConfirm={handleDeleteConfirmed}
            onCancel={() => setConfirmingDelete(false)}
          />
        </div>
      )}
      {deleteError && (
        <div className="mt-2">
          <ErrorBanner error={deleteError} />
        </div>
      )}

      {editing && <EditResourceModal resource={resource} onClose={() => setEditing(false)} />}
    </li>
  );
}

function TextResourcesTab() {
  const resourcesQuery = useResources();
  const resources = resourcesQuery.data?.results ?? [];
  const [showAddModal, setShowAddModal] = useState(false);

  return (
    <section aria-labelledby="resources-heading" className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id="resources-heading" className="text-lg font-medium text-gray-900">
          Text resources
          {resourcesQuery.isSuccess && <span aria-hidden="true" className="ml-2 text-sm font-normal text-gray-400">({resources.length})</span>}
        </h2>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Add resource
        </button>
      </div>

      {resourcesQuery.isLoading && <p className="text-sm text-gray-500">Loading resources…</p>}
      {resourcesQuery.isError && <ErrorBanner error={resourcesQuery.error} />}

      {resourcesQuery.isSuccess && resources.length === 0 && (
        <EmptyState title="No resources yet" message="Add a resource to get started." />
      )}

      {resourcesQuery.isSuccess && resources.length > 0 && (
        <ul aria-label="Resources" className="max-h-[36rem] overflow-y-auto space-y-3 pr-1">
          {resources.map((resource) => (
            <ResourceRow key={resource.id} resource={resource} />
          ))}
        </ul>
      )}

      {showAddModal && <AddResourceModal onClose={() => setShowAddModal(false)} />}
    </section>
  );
}

// -----------------------------------------------------------------------
// Few-shot examples

const emptyFewshotForm = {
  name: '',
  phase: '2',
  inputMode: 'resource',
  inputResourceId: '',
  inputText: '',
  outputMode: 'resource',
  outputResourceId: '',
  outputText: '',
};

function fewshotFormFromRow(fewshot) {
  return {
    name: fewshot.name,
    phase: String(fewshot.phase),
    inputMode: fewshot.input_file != null ? 'resource' : 'text',
    inputResourceId: fewshot.input_file != null ? String(fewshot.input_file) : '',
    inputText: fewshot.input_text || '',
    outputMode: fewshot.output_file != null ? 'resource' : 'text',
    outputResourceId: fewshot.output_file != null ? String(fewshot.output_file) : '',
    outputText: fewshot.output_text || '',
  };
}

function FewShotModal({ fewshot, resources, onClose }) {
  const isEdit = fewshot != null;
  const createFewShot = useCreateFewShot();
  const updateFewShot = useUpdateFewShot();
  const mutation = isEdit ? updateFewShot : createFewShot;
  const [form, setForm] = useState(() => (isEdit ? fewshotFormFromRow(fewshot) : emptyFewshotForm));
  const idPrefix = isEdit ? `fewshot-edit-${fewshot.id}` : 'fewshot-add';

  async function handleSubmit(event) {
    event.preventDefault();
    const payload = {
      phase: Number(form.phase),
      name: form.name,
      input_text: form.inputMode === 'text' ? form.inputText : '',
      input_file: form.inputMode === 'resource' && form.inputResourceId ? Number(form.inputResourceId) : null,
      output_text: form.outputMode === 'text' ? form.outputText : '',
      output_file: form.outputMode === 'resource' && form.outputResourceId ? Number(form.outputResourceId) : null,
    };
    try {
      if (isEdit) {
        await updateFewShot.mutateAsync({ id: fewshot.id, ...payload });
      } else {
        await createFewShot.mutateAsync(payload);
      }
      onClose();
    } catch {
      // Surfaced below via mutation.isError/error.
    }
  }

  return (
    <Modal title={isEdit ? `Edit ${fewshot.name}` : 'New few-shot'} onClose={onClose} wide>
      <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={`${idPrefix}-name`} className="block text-sm font-medium text-gray-700">
            Name
          </label>
          <input
            id={`${idPrefix}-name`}
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-phase`} className="block text-sm font-medium text-gray-700">
            Phase
          </label>
          <select
            id={`${idPrefix}-phase`}
            value={form.phase}
            onChange={(event) => setForm((current) => ({ ...current, phase: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
          >
            {PHASE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-gray-700">Input</legend>
          <div className="flex gap-4 text-sm text-gray-600">
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name={`${idPrefix}-input-mode`}
                checked={form.inputMode === 'resource'}
                onChange={() => setForm((current) => ({ ...current, inputMode: 'resource' }))}
              />
              Existing resource
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name={`${idPrefix}-input-mode`}
                checked={form.inputMode === 'text'}
                onChange={() => setForm((current) => ({ ...current, inputMode: 'text' }))}
              />
              Type text
            </label>
          </div>
          {form.inputMode === 'resource' ? (
            <select
              aria-label="Input resource"
              value={form.inputResourceId}
              onChange={(event) => setForm((current) => ({ ...current, inputResourceId: event.target.value }))}
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            >
              <option value="">Select a resource…</option>
              {resources.map((resource) => (
                <option key={resource.id} value={resource.id}>
                  {resource.name}
                </option>
              ))}
            </select>
          ) : (
            <textarea
              aria-label="Input text"
              value={form.inputText}
              onChange={(event) => setForm((current) => ({ ...current, inputText: event.target.value }))}
              rows={3}
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          )}
        </fieldset>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-gray-700">Output</legend>
          <div className="flex gap-4 text-sm text-gray-600">
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name={`${idPrefix}-output-mode`}
                checked={form.outputMode === 'resource'}
                onChange={() => setForm((current) => ({ ...current, outputMode: 'resource' }))}
              />
              Existing resource
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name={`${idPrefix}-output-mode`}
                checked={form.outputMode === 'text'}
                onChange={() => setForm((current) => ({ ...current, outputMode: 'text' }))}
              />
              Type text
            </label>
          </div>
          {form.outputMode === 'resource' ? (
            <select
              aria-label="Output resource"
              value={form.outputResourceId}
              onChange={(event) => setForm((current) => ({ ...current, outputResourceId: event.target.value }))}
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            >
              <option value="">Select a resource…</option>
              {resources.map((resource) => (
                <option key={resource.id} value={resource.id}>
                  {resource.name}
                </option>
              ))}
            </select>
          ) : (
            <textarea
              aria-label="Output text"
              value={form.outputText}
              onChange={(event) => setForm((current) => ({ ...current, outputText: event.target.value }))}
              rows={3}
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          )}
        </fieldset>

        {mutation.isError && (
          <div className="sm:col-span-2">
            <ErrorBanner error={mutation.error} />
          </div>
        )}

        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={mutation.isPending || !form.name}
            className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {mutation.isPending ? 'Saving…' : isEdit ? 'Save' : 'Pair few-shot'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FewShotCard({ fewshot, onEdit }) {
  const deleteFewShot = useDeleteFewShot();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  async function handleDeleteConfirmed() {
    setDeleteError(null);
    try {
      await deleteFewShot.mutateAsync(fewshot.id);
      setConfirmingDelete(false);
    } catch (error) {
      setDeleteError(error);
    }
  }

  return (
    <li className="space-y-2 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-gray-900">{fewshot.name}</p>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
              fewshot.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'
            }`}
          >
            {fewshot.is_active ? 'Active' : 'Inactive'}
          </span>
          <button
            type="button"
            onClick={onEdit}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            disabled={deleteFewShot.isPending}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Delete
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <CollapsibleText label="Resolved input" text={fewshot.resolved_input} />
        <CollapsibleText label="Resolved output" text={fewshot.resolved_output} />
      </div>

      {confirmingDelete && (
        <ConfirmDialog
          title={`Delete "${fewshot.name}"?`}
          pending={deleteFewShot.isPending}
          onConfirm={handleDeleteConfirmed}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
      {deleteError && <ErrorBanner error={deleteError} />}
    </li>
  );
}

function FewShotsTab() {
  const resourcesQuery = useResources();
  const fewshotsQuery = useFewShots();
  const resources = resourcesQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];
  const [modalState, setModalState] = useState(null); // null | 'new' | a fewshot row

  return (
    <section aria-labelledby="fewshots-heading" className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="fewshots-heading" className="text-lg font-medium text-gray-900">
            Few-shot examples
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Phase 2 pairs are system description → scenarios; phase 3 pairs are scenario → labels.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModalState('new')}
          className="shrink-0 rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          New few-shot
        </button>
      </div>

      {fewshotsQuery.isLoading && <p className="text-sm text-gray-500">Loading few-shots…</p>}
      {fewshotsQuery.isError && <ErrorBanner error={fewshotsQuery.error} />}

      {fewshotsQuery.isSuccess && fewshots.length === 0 && (
        <EmptyState title="No few-shots yet" message="Pair a resource or typed text above to create one." />
      )}

      {fewshotsQuery.isSuccess &&
        PHASE_OPTIONS.map((option) => {
          const items = fewshots.filter((fewshot) => fewshot.phase === Number(option.value));
          if (items.length === 0) return null;
          return (
            <div key={option.value}>
              <h3 className="text-sm font-semibold text-gray-700">
                {option.label}
                <span aria-hidden="true" className="ml-2 font-normal text-gray-400">({items.length})</span>
              </h3>
              <ul aria-label={option.label} className="mt-2 max-h-[36rem] overflow-y-auto space-y-3 pr-1">
                {items.map((fewshot) => (
                  <FewShotCard key={fewshot.id} fewshot={fewshot} onEdit={() => setModalState(fewshot)} />
                ))}
              </ul>
            </div>
          );
        })}

      {modalState != null && (
        <FewShotModal
          fewshot={modalState === 'new' ? null : modalState}
          resources={resources}
          onClose={() => setModalState(null)}
        />
      )}
    </section>
  );
}

// -----------------------------------------------------------------------

export default function Resources() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const activeTab = TABS.some((tab) => tab.value === requestedTab) ? requestedTab : 'corpus';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Resources</h1>
        <p className="mt-1 text-sm text-gray-500">
          Systems &amp; scenarios are the structured corpus: seed and target systems for generation, and the
          scenarios phase 3 labels (seed and manual rows are editable, generated rows are read-only). Few-shots are
          example input/output pairs for phase 2 and phase 3 prompts. Text resources are the source documents,
          which a prompt can pull in with <code>resource(&quot;name&quot;)</code> and a few-shot can use as its
          input or output.
        </p>
      </div>

      <nav aria-label="Resource sections" className="flex gap-6 border-b border-gray-200">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => setSearchParams({ tab: tab.value })}
            aria-current={activeTab === tab.value ? 'page' : undefined}
            className={`-mb-px border-b-2 px-1 pb-2 text-sm font-medium ${
              activeTab === tab.value
                ? 'border-brand-600 text-brand-700'
                : 'border-transparent text-gray-500 hover:text-gray-900'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === 'resources' && <TextResourcesTab />}
      {activeTab === 'fewshots' && <FewShotsTab />}
      {activeTab === 'corpus' && <Corpus />}
    </div>
  );
}
