import { useState } from 'react';
import { useAuth } from '../contexts/auth-context';
import { useSettings, useUpdateSettings } from '../api/queries';
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
    try {
      await updateSettings.mutateAsync({ openrouter_key: newKey });
      setNewKey('');
      setSaved(true);
    } catch {
      // Surfaced below via updateSettings.isError/error - nothing further
      // to do here.
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
        </>
      )}
    </div>
  );
}
