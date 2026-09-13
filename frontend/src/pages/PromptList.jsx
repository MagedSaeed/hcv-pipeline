import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import { useDeletePromptTemplate, usePromptTemplates } from '../api/queries';
import { extractErrorMessage } from '../api/errors';
import ConfirmDialog from '../components/ConfirmDialog';
import ErrorBanner from '../components/ErrorBanner';

const PHASES = [
  { value: 2, label: 'Phase 2 — generation' },
  { value: 3, label: 'Phase 3 — labelling' },
];

// Groups a phase-scoped list of template rows by name, latest version
// first (mirrors prompts.models.PromptTemplate.Meta.ordering).
function groupByName(templates) {
  const groups = new Map();
  for (const template of templates) {
    if (!groups.has(template.name)) groups.set(template.name, []);
    groups.get(template.name).push(template);
  }
  for (const versions of groups.values()) versions.sort((a, b) => b.version - a.version);
  return groups;
}

// One row per prompt NAME, not per version - View/Edit go to the latest
// version; Delete removes every version one by one (task D). A version
// still pinned by an experiment or a run comes back as a 400 and is kept.
function PromptGroupRow({ phase, name, versions, isStaff }) {
  const [expanded, setExpanded] = useState(false);
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);
  const [keptVersions, setKeptVersions] = useState([]);
  const [confirmingVersionId, setConfirmingVersionId] = useState(null);
  const deleteTemplate = useDeletePromptTemplate();

  const latest = versions[0];
  const activeVersion = versions.find((version) => version.is_active) ?? null;

  async function handleDeleteAllConfirmed() {
    const kept = [];
    for (const version of versions) {
      try {
        // eslint-disable-next-line no-await-in-loop -- deletes must go one
        // by one (spec: "DELETE each, one by one").
        await deleteTemplate.mutateAsync(version.id);
      } catch (error) {
        kept.push({ version: version.version, message: extractErrorMessage(error) });
      }
    }
    setConfirmingDeleteAll(false);
    setKeptVersions(kept);
  }

  async function handleDeleteVersionConfirmed(versionId) {
    try {
      await deleteTemplate.mutateAsync(versionId);
      setConfirmingVersionId(null);
    } catch {
      // Surfaced below via deleteTemplate.isError/error - dialog stays open.
    }
  }

  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-gray-900">{name}</span>
            {phase === 2 && latest.variant && (
              <span className="inline-flex items-center rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
                Variant {latest.variant}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500">
            v{latest.version}
            {activeVersion ? ` · active: v${activeVersion.version}` : ' · no active version'}
            {' · edited '}
            {new Date(latest.created_at).toLocaleString()}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Link
            to={`/prompts/${latest.id}`}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            View
          </Link>
          {isStaff && (
            <Link
              to={`/prompts/${latest.id}/edit`}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              Edit
            </Link>
          )}
          {isStaff && !confirmingDeleteAll && (
            <button
              type="button"
              onClick={() => setConfirmingDeleteAll(true)}
              className="rounded-md border border-red-600 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
            >
              Delete
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            aria-expanded={expanded}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
          >
            Versions ({versions.length})
          </button>
        </div>
      </div>

      {confirmingDeleteAll && (
        <ConfirmDialog
          title={`Delete all ${versions.length} version(s) of “${name}”?`}
          message="A version still referenced by an experiment or a run is kept — you'll see which and why."
          confirmLabel="Confirm"
          cancelLabel="Never mind"
          pending={deleteTemplate.isPending}
          onConfirm={handleDeleteAllConfirmed}
          onCancel={() => setConfirmingDeleteAll(false)}
        />
      )}

      {keptVersions.length > 0 && (
        <div role="note" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Kept {keptVersions.length} of {versions.length} version(s) — still in use:{' '}
          {keptVersions.map((kept) => `v${kept.version} (${kept.message})`).join('; ')}
        </div>
      )}

      {expanded && (
        <ul className="divide-y divide-gray-200 rounded-md border border-gray-200 bg-gray-50">
          {versions.map((version) => (
            <li key={version.id} className="space-y-2 px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  Version {version.version}
                  {version.is_active && (
                    <span className="ml-2 inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                      active
                    </span>
                  )}
                  <span className="ml-2 text-gray-500">{new Date(version.created_at).toLocaleString()}</span>
                </span>
                <div className="flex items-center gap-2">
                  <Link
                    to={`/prompts/${version.id}`}
                    className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                  >
                    View
                  </Link>
                  {isStaff && confirmingVersionId !== version.id && (
                    <button
                      type="button"
                      onClick={() => setConfirmingVersionId(version.id)}
                      className="rounded-md border border-red-600 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
              {confirmingVersionId === version.id && (
                <ConfirmDialog
                  title={`Delete version ${version.version}?`}
                  message="This version is gone for good. If an experiment or a run still pins it, the API refuses the delete instead."
                  confirmLabel="Confirm"
                  cancelLabel="Never mind"
                  pending={deleteTemplate.isPending}
                  onConfirm={() => handleDeleteVersionConfirmed(version.id)}
                  onCancel={() => setConfirmingVersionId(null)}
                />
              )}
              {confirmingVersionId === version.id && deleteTemplate.isError && (
                <ErrorBanner error={deleteTemplate.error} />
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function PromptList() {
  const { isStaff } = useAuth();
  const templatesQuery = usePromptTemplates({});
  const templates = templatesQuery.data?.results ?? [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Prompts</h1>
        <p className="mt-1 text-sm text-gray-500">
          One row per prompt name. Open a row to browse every version, or Edit to write the next
          one.
        </p>
      </div>

      {templatesQuery.isLoading && <p className="text-sm text-gray-500">Loading prompts…</p>}
      {templatesQuery.isError && <ErrorBanner error={templatesQuery.error} />}

      {templatesQuery.isSuccess &&
        PHASES.map((phaseOption) => {
          const phaseTemplates = templates.filter((template) => template.phase === phaseOption.value);
          const groups = groupByName(phaseTemplates);
          const names = [...groups.keys()].sort((a, b) => a.localeCompare(b));
          return (
            <section
              key={phaseOption.value}
              aria-labelledby={`phase-${phaseOption.value}-heading`}
              className="space-y-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id={`phase-${phaseOption.value}-heading`} className="text-lg font-medium text-gray-900">
                  {phaseOption.label}
                </h2>
                {isStaff && (
                  <Link
                    to={`/prompts/new?phase=${phaseOption.value}`}
                    className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
                  >
                    New prompt
                  </Link>
                )}
              </div>
              {names.length === 0 ? (
                <p className="text-sm text-gray-500">No phase {phaseOption.value} prompts yet.</p>
              ) : (
                <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
                  {names.map((name) => (
                    <PromptGroupRow
                      key={name}
                      phase={phaseOption.value}
                      name={name}
                      versions={groups.get(name)}
                      isStaff={isStaff}
                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
    </div>
  );
}
