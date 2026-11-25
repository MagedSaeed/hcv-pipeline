import { vi } from 'vitest';

// jsdom has no matchMedia; this makes every media query match, as on a wide screen.
export function stubWideScreen() {
  vi.stubGlobal('matchMedia', (query) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}
