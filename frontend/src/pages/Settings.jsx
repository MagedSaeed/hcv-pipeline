import { useState } from 'react';
import { useAuth } from '../contexts/auth-context';
import { useSettings, useUpdateSettings } from '../api/queries';
import ConfirmDialog from '../components/ConfirmDialog';
import ErrorBanner from '../components/ErrorBanner';

// backend/core/keys.py's key_source(): the stored (Settings-screen) key
// wins over the environment variable, which is why "settings" is listed
// first here too - this mirrors that resolution order, not just alphabetizes.
const SOURCE_LABELS = {
  settings: 'Set on this Settings screen',
  environment: 'From the OPENROUTER_API_KEY environment variable',
  none: 'Not configured — generation and labelling will fail until a key is set',
};

// Staff only (backend/core/views.py's AppSettingsView uses IsAdminUser).
// Never handles the real key value: the GET response only ever carries the
// masked form (`openrouter_key_masked`, from `core.keys.mask_key` - first
// and last 4 characters, "..." between), and a save posts whatever the
// operator just typed straight through without ever reading it back.
export default function Settings() {
  const { isStaff } = useAuth();

  const settingsQuery = useSettings({ enabled: isStaff });
  const updateSettings = useUpdateSettings();

  const [newKey, setNewKey] = useState('');
  const [saved, setSaved] = useState(false);
  // Task 10.6 (folded-in walkthrough finding): before this, Save disabled
  // itself on an empty field ("disabled={... || !newKey}"), so once a key
  // was ever saved on this screen there was NO way to make the app fall
  // back to `.env` again — the exact trap that failed the walkthrough's
  // first iterate attempt with a 401. The API always allowed this
  // (`openrouter_key` is `allow_blank=True`; a PATCH with `""` clears the
  // stored key and `key_source` becomes `"environment"`, core/keys.py) — the
  // gap was only ever this screen never sending that request. This is a
  // SEPARATE action from Save, not "Save with an empty field made to work",
  // so an operator can never clear the key by accident while merely leaving
  // the input blank.
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [cleared, setCleared] = useState(false);

  if (!isStaff) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>
        <p role="status" className="text-sm text-gray-500">
          Only staff can view or change application settings.
        </p>
      </div>
    );
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSaved(false);
    setCleared(false);
    try {
      await updateSettings.mutateAsync({ openrouter_key: newKey });
      setNewKey('');
      setSaved(true);
    } catch {
      // Surfaced below via updateSettings.isError/error - nothing further
      // to do here.
    }
  }

  async function handleClearConfirmed() {
    setSaved(false);
    setCleared(false);
    try {
      // The exact same PATCH `openrouter_key: ""` the API has always
      // accepted (`allow_blank=True`) - never a distinct backend action.
      await updateSettings.mutateAsync({ openrouter_key: '' });
      setNewKey('');
      setConfirmingClear(false);
      setCleared(true);
    } catch {
      // Surfaced below via updateSettings.isError/error - dialog stays open.
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">
          The OpenRouter API key used for every generation and labelling call.
        </p>
      </div>

      {settingsQuery.isLoading && <p className="text-sm text-gray-500">Loading…</p>}
      {settingsQuery.isError && <ErrorBanner error={settingsQuery.error} />}

      {settingsQuery.isSuccess && (
        <>
          <dl className="grid grid-cols-1 gap-4 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Current key
              </dt>
              {/* Only ever the masked value the API returns - never the real
                  secret, which this screen is never given in the first place. */}
              <dd className="mt-1 font-mono text-sm text-gray-900">
                {settingsQuery.data.openrouter_key_masked || 'Not set'}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Source in effect
              </dt>
              <dd className="mt-1 text-sm text-gray-900">
                {SOURCE_LABELS[settingsQuery.data.key_source] ?? settingsQuery.data.key_source}
              </dd>
            </div>
          </dl>

          <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
            <div>
              <label htmlFor="openrouter-key" className="block text-sm font-medium text-gray-700">
                Set a new key
              </label>
              <input
                id="openrouter-key"
                type="password"
                autoComplete="off"
                value={newKey}
                onChange={(event) => setNewKey(event.target.value)}
                placeholder="sk-or-…"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
            </div>

            {updateSettings.isError && <ErrorBanner error={updateSettings.error} />}
            {saved && (
              <p role="status" className="text-sm text-green-700">
                Key updated.
              </p>
            )}

            <button
              type="submit"
              disabled={updateSettings.isPending || !newKey}
              className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {updateSettings.isPending ? 'Saving…' : 'Save key'}
            </button>
          </form>

          {/* Task 10.6: a SEPARATE, explicit action from the form above - Save
              staying disabled on an empty field must never be the only way
              this key can move, or an admin-entered one can never be
              un-done and the app can never fall back to `.env` again (the
              walkthrough's own first-run 401). This never handles or shows
              the real key either; it only ever sends the empty string the
              API already accepts. */}
          {settingsQuery.data.key_source === 'settings' && (
            <div className="space-y-2 rounded-lg border border-gray-200 bg-white p-4">
              <p className="text-sm text-gray-600">
                A key is currently set on this screen, which always wins over the environment
                variable. Clearing it falls back to <code>OPENROUTER_API_KEY</code> from the
                environment, if one is configured there.
              </p>
              {!confirmingClear && (
                <button
                  type="button"
                  onClick={() => setConfirmingClear(true)}
                  className="rounded-md border border-red-600 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
                >
                  Clear — use the environment key
                </button>
              )}
              {confirmingClear && (
                <ConfirmDialog
                  title="Clear the OpenRouter key set on this screen?"
                  message="Every generation and labelling call will fall back to the OPENROUTER_API_KEY environment variable — or fail immediately with a clear message if none is configured there. This does not touch the environment variable itself."
                  confirmLabel="Confirm"
                  cancelLabel="Never mind"
                  pending={updateSettings.isPending}
                  onConfirm={handleClearConfirmed}
                  onCancel={() => setConfirmingClear(false)}
                />
              )}
            </div>
          )}

          {cleared && (
            <p role="status" className="text-sm text-green-700">
              Cleared. Source in effect is now:{' '}
              {SOURCE_LABELS[settingsQuery.data.key_source] ?? settingsQuery.data.key_source}
            </p>
          )}
        </>
      )}
    </div>
  );
}
