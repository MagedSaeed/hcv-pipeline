import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import ErrorBanner from '../ErrorBanner';

describe('ErrorBanner', () => {
  it("shows the server's own error message, not a generic string", () => {
    // Shaped like the 503 the backend actually returns when no OpenRouter
    // key is configured (core/keys.py's MissingAPIKey message, surfaced
    // through pipeline/views.py) - carries a machine-readable `code`
    // alongside `detail`; this component only needs to render `detail`.
    const error = {
      response: {
        status: 503,
        data: {
          detail:
            'No OpenRouter API key is configured. Set one on the Settings screen, or provide OPENROUTER_API_KEY in the environment.',
          code: 'missing_api_key',
        },
      },
    };

    render(<ErrorBanner error={error} fallback="Something went wrong." />);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/no openrouter api key is configured/i);
    expect(alert).not.toHaveTextContent('Something went wrong.');
  });

  it('shows a 409 conflict message naming exactly what blocks the action', () => {
    const error = {
      response: {
        status: 409,
        data: {
          detail: "'Baseline' has runs recorded against it and cannot be deleted.",
          code: 'experiment_has_runs',
        },
      },
    };

    render(<ErrorBanner error={error} />);

    expect(screen.getByRole('alert')).toHaveTextContent(/has runs recorded against it and cannot be deleted/i);
  });

  it('falls back to the given fallback only when the server gives no usable message', () => {
    render(<ErrorBanner error={{}} fallback="Custom fallback message." />);

    expect(screen.getByRole('alert')).toHaveTextContent('Custom fallback message.');
  });
});
