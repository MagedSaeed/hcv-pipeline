import { useState } from 'react';
import {
  useCreateFewShot,
  useCreateResource,
  useDeleteResource,
  useFewShots,
  useResources,
} from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';

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

const emptyUploadForm = { name: '', kind: 'other', description: '', file: null };
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

export default function Resources() {
  const resourcesQuery = useResources();
  const fewshotsQuery = useFewShots();

  const createResource = useCreateResource();
  const deleteResource = useDeleteResource();
  const createFewShot = useCreateFewShot();

  const [uploadForm, setUploadForm] = useState(emptyUploadForm);
  const [fewshotForm, setFewshotForm] = useState(emptyFewshotForm);
  // Per-resource: a delete a few-shot still references comes back as a 400,
  // not a crash - keyed by resource id so one row's refusal doesn't hide
  // another row's, or get cleared by an unrelated action elsewhere on the
  // page.
  const [deleteErrors, setDeleteErrors] = useState({});

  const resources = resourcesQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];

  async function handleUpload(event) {
    event.preventDefault();
    const body = new FormData();
    body.append('name', uploadForm.name);
    body.append('kind', uploadForm.kind);
    if (uploadForm.description) body.append('description', uploadForm.description);
    if (uploadForm.file) body.append('file', uploadForm.file);
    try {
      await createResource.mutateAsync(body);
      setUploadForm(emptyUploadForm);
    } catch {
      // Surfaced below via createResource.isError/error.
    }
  }

  async function handleDelete(resource) {
    setDeleteErrors((previous) => {
      const next = { ...previous };
      delete next[resource.id];
      return next;
    });
    try {
      await deleteResource.mutateAsync(resource.id);
    } catch (error) {
      setDeleteErrors((previous) => ({ ...previous, [resource.id]: error }));
    }
  }

  async function handlePairFewShot(event) {
    event.preventDefault();
    const payload = {
      phase: Number(fewshotForm.phase),
      name: fewshotForm.name,
      input_text: fewshotForm.inputMode === 'text' ? fewshotForm.inputText : '',
      input_file: fewshotForm.inputMode === 'resource' && fewshotForm.inputResourceId
        ? Number(fewshotForm.inputResourceId)
        : null,
      output_text: fewshotForm.outputMode === 'text' ? fewshotForm.outputText : '',
      output_file: fewshotForm.outputMode === 'resource' && fewshotForm.outputResourceId
        ? Number(fewshotForm.outputResourceId)
        : null,
    };
    try {
      await createFewShot.mutateAsync(payload);
      setFewshotForm(emptyFewshotForm);
    } catch {
      // Surfaced below via createFewShot.isError/error.
    }
  }

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Resources</h1>
        <p className="mt-1 text-sm text-gray-500">
          Upload text resources and pair them into few-shot examples for phase 2 and phase 3
          prompts.
        </p>
      </div>

      <section aria-labelledby="upload-heading" className="space-y-3">
        <h2 id="upload-heading" className="text-lg font-medium text-gray-900">
          Upload a resource
        </h2>
        <form
          onSubmit={handleUpload}
          className="grid grid-cols-1 gap-4 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2"
        >
          <div>
            <label htmlFor="resource-name" className="block text-sm font-medium text-gray-700">
              Name
            </label>
            <input
              id="resource-name"
              value={uploadForm.name}
              onChange={(event) => setUploadForm((form) => ({ ...form, name: event.target.value }))}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          </div>
          <div>
            <label htmlFor="resource-kind" className="block text-sm font-medium text-gray-700">
              Kind
            </label>
            <select
              id="resource-kind"
              value={uploadForm.kind}
              onChange={(event) => setUploadForm((form) => ({ ...form, kind: event.target.value }))}
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
            <label htmlFor="resource-file" className="block text-sm font-medium text-gray-700">
              File (.txt, .md, .json — UTF-8 text)
            </label>
            <input
              id="resource-file"
              type="file"
              onChange={(event) =>
                setUploadForm((form) => ({ ...form, file: event.target.files?.[0] ?? null }))
              }
              className="mt-1 block w-full text-sm text-gray-700"
            />
          </div>
          <div>
            <label htmlFor="resource-description" className="block text-sm font-medium text-gray-700">
              Description (optional)
            </label>
            <input
              id="resource-description"
              value={uploadForm.description}
              onChange={(event) =>
                setUploadForm((form) => ({ ...form, description: event.target.value }))
              }
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          </div>

          {createResource.isError && (
            <div className="sm:col-span-2">
              <ErrorBanner error={createResource.error} />
            </div>
          )}

          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={createResource.isPending || !uploadForm.name}
              className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {createResource.isPending ? 'Uploading…' : 'Upload'}
            </button>
          </div>
        </form>
      </section>

      <section aria-labelledby="resources-heading" className="space-y-3">
        <h2 id="resources-heading" className="text-lg font-medium text-gray-900">
          Resources
        </h2>

        {resourcesQuery.isLoading && <p className="text-sm text-gray-500">Loading resources…</p>}
        {resourcesQuery.isError && <ErrorBanner error={resourcesQuery.error} />}

        {resourcesQuery.isSuccess && resources.length === 0 && (
          <EmptyState title="No resources yet" message="Upload a text file to get started." />
        )}

        {resourcesQuery.isSuccess && resources.length > 0 && (
          <ul aria-label="Resources" className="space-y-3">
            {resources.map((resource) => (
              <li key={resource.id} className="rounded-lg border border-gray-200 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-gray-900">{resource.name}</p>
                    {resource.description && (
                      <p className="text-xs text-gray-500">{resource.description}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <ResourceKindBadge kind={resource.kind} />
                    <button
                      type="button"
                      onClick={() => handleDelete(resource)}
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

                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-700">
                  {resource.text || '(no extracted text)'}
                </pre>

                {deleteErrors[resource.id] && <ErrorBanner error={deleteErrors[resource.id]} />}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="fewshot-pairing-heading" className="space-y-3">
        <h2 id="fewshot-pairing-heading" className="text-lg font-medium text-gray-900">
          Pair a few-shot example
        </h2>
        <form
          onSubmit={handlePairFewShot}
          className="grid grid-cols-1 gap-4 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2"
        >
          <div>
            <label htmlFor="fewshot-name" className="block text-sm font-medium text-gray-700">
              Name
            </label>
            <input
              id="fewshot-name"
              value={fewshotForm.name}
              onChange={(event) => setFewshotForm((form) => ({ ...form, name: event.target.value }))}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
            />
          </div>
          <div>
            <label htmlFor="fewshot-phase" className="block text-sm font-medium text-gray-700">
              Phase
            </label>
            <select
              id="fewshot-phase"
              value={fewshotForm.phase}
              onChange={(event) => setFewshotForm((form) => ({ ...form, phase: event.target.value }))}
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
                  name="input-mode"
                  checked={fewshotForm.inputMode === 'resource'}
                  onChange={() => setFewshotForm((form) => ({ ...form, inputMode: 'resource' }))}
                />
                Existing resource
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="radio"
                  name="input-mode"
                  checked={fewshotForm.inputMode === 'text'}
                  onChange={() => setFewshotForm((form) => ({ ...form, inputMode: 'text' }))}
                />
                Type text
              </label>
            </div>
            {fewshotForm.inputMode === 'resource' ? (
              <select
                aria-label="Input resource"
                value={fewshotForm.inputResourceId}
                onChange={(event) =>
                  setFewshotForm((form) => ({ ...form, inputResourceId: event.target.value }))
                }
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
                value={fewshotForm.inputText}
                onChange={(event) =>
                  setFewshotForm((form) => ({ ...form, inputText: event.target.value }))
                }
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
                  name="output-mode"
                  checked={fewshotForm.outputMode === 'resource'}
                  onChange={() => setFewshotForm((form) => ({ ...form, outputMode: 'resource' }))}
                />
                Existing resource
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="radio"
                  name="output-mode"
                  checked={fewshotForm.outputMode === 'text'}
                  onChange={() => setFewshotForm((form) => ({ ...form, outputMode: 'text' }))}
                />
                Type text
              </label>
            </div>
            {fewshotForm.outputMode === 'resource' ? (
              <select
                aria-label="Output resource"
                value={fewshotForm.outputResourceId}
                onChange={(event) =>
                  setFewshotForm((form) => ({ ...form, outputResourceId: event.target.value }))
                }
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
                value={fewshotForm.outputText}
                onChange={(event) =>
                  setFewshotForm((form) => ({ ...form, outputText: event.target.value }))
                }
                rows={3}
                className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm"
              />
            )}
          </fieldset>

          {createFewShot.isError && (
            <div className="sm:col-span-2">
              <ErrorBanner error={createFewShot.error} />
            </div>
          )}

          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={createFewShot.isPending || !fewshotForm.name}
              className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {createFewShot.isPending ? 'Pairing…' : 'Pair few-shot'}
            </button>
          </div>
        </form>
      </section>

      <section aria-labelledby="fewshots-heading" className="space-y-3">
        <h2 id="fewshots-heading" className="text-lg font-medium text-gray-900">
          Few-shot examples
        </h2>

        {fewshotsQuery.isLoading && <p className="text-sm text-gray-500">Loading few-shots…</p>}
        {fewshotsQuery.isError && <ErrorBanner error={fewshotsQuery.error} />}

        {fewshotsQuery.isSuccess && fewshots.length === 0 && (
          <EmptyState title="No few-shots yet" message="Pair two resources above to create one." />
        )}

        {fewshotsQuery.isSuccess && fewshots.length > 0 && (
          <ul aria-label="Few-shot examples" className="space-y-3">
            {fewshots.map((fewshot) => (
              <li key={fewshot.id} className="rounded-lg border border-gray-200 bg-white p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-gray-900">{fewshot.name}</p>
                  <span className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700">
                    {PHASE_OPTIONS.find((option) => Number(option.value) === fewshot.phase)?.label ??
                      `Phase ${fewshot.phase}`}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
                      Resolved input
                    </p>
                    <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-700">
                      {fewshot.resolved_input}
                    </pre>
                  </div>
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
                      Resolved output
                    </p>
                    <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-700">
                      {fewshot.resolved_output}
                    </pre>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
