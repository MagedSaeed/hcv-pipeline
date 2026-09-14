import { useState } from 'react';
import { formatJsonLike } from '../utils/json';

// A read-only, monospace viewer for prompt/response text. Scrolls rather
// than clips: a max-height with overflow-auto keeps a very long rendered
// prompt or raw response readable on screen without ever cutting content
// out of the DOM - "untruncated and copyable" (spec §10, Run detail's call
// inspector) means the full text stays reachable, not merely visible.
// Shared here so the iterate panel (task 5) and the call inspector (task 7)
// render prompts/responses identically rather than each screen inventing
// its own <pre> styling.
export default function CodeBlock({ title, code, emptyText = '(empty)' }) {
  const [copied, setCopied] = useState(false);
  const received = code ?? '';
  const formatted = formatJsonLike(received);
  const [showReceived, setShowReceived] = useState(false);
  const text = formatted !== null && !showReceived ? formatted : received;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be denied, unavailable outside a secure
      // context, or simply absent (as in a test environment) - copying is a
      // convenience the rest of the screen never depends on succeeding.
    }
  }

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-gray-200 bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-gray-200 px-3 py-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</p>
        <div className="flex items-center gap-2">
        {formatted !== null && (
          <button
            type="button"
            onClick={() => setShowReceived((current) => !current)}
            className="rounded border border-gray-300 px-2 py-0.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
          >
            {showReceived ? 'Show formatted' : 'Show as received'}
          </button>
        )}
        <button
          type="button"
          onClick={handleCopy}
          className="rounded border border-gray-300 px-2 py-0.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
        </div>
      </div>
      <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-words p-3 text-xs text-gray-800">
        {text || emptyText}
      </pre>
    </div>
  );
}
