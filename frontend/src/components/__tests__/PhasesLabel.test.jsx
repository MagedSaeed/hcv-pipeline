import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import PhasesLabel from '../PhasesLabel';

describe('PhasesLabel', () => {
  it.each([
    [[2, 3], 'Phase 2 (generate) → Phase 3 (label)'],
    [[2], 'Phase 2 only (generate, no labelling)'],
    [[3], 'Phase 3 only (label chosen scenarios)'],
    [undefined, 'Phase 2 (generate) → Phase 3 (label)'],
  ])('names %j', (phases, text) => {
    render(<p><PhasesLabel phases={phases} /></p>);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
});
