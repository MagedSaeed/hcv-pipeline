import { extractErrorMessage } from '../api/errors';

// Renders the server's own error message, never a generic placeholder - the
// backend goes out of its way to return actionable text (a missing API key,
// a 409 naming exactly what blocks a delete), and swallowing that into
// "Something went wrong" throws away the whole point of writing it.
// `extractErrorMessage` (task 1, src/api/errors.js) already knows how to
// pull the right string out of every DRF error shape this API produces;
// this component's only job is to display whatever it returns.
export default function ErrorBanner({ error, fallback }) {
  const message = extractErrorMessage(error, fallback);

  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}
