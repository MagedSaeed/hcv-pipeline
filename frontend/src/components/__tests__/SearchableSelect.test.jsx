import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SearchableSelect from '../SearchableSelect';

const OPTIONS = [
  { value: '1', label: 'Alpha run', description: 'unbiased · gpt-4o' },
  { value: '2', label: 'Beta run', description: 'guided · claude' },
  { value: '3', label: 'Gamma run', description: 'unbiased · claude' },
];

function ControlledSelect(props) {
  const [value, setValue] = useState(props.value ?? '');
  return <SearchableSelect {...props} value={value} onChange={setValue} />;
}

describe('SearchableSelect', () => {
  it('shows the placeholder when nothing is chosen, and the search box on open', async () => {
    const user = userEvent.setup();
    render(
      <SearchableSelect id="f" label="Run" value="" onChange={() => {}} options={OPTIONS} placeholder="Choose a run…" />
    );

    const field = screen.getByLabelText('Run');
    expect(field).toHaveTextContent('Choose a run…');
    expect(field).toHaveAttribute('role', 'combobox');
    expect(field).toHaveAttribute('aria-expanded', 'false');

    await user.click(field);
    expect(field).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('filters by label and description, requiring every typed word to match', async () => {
    const user = userEvent.setup();
    render(
      <SearchableSelect id="f" label="Run" value="" onChange={() => {}} options={OPTIONS} placeholder="Choose…" />
    );

    await user.click(screen.getByLabelText('Run'));
    await user.type(screen.getByRole('searchbox'), 'unbiased');

    expect(screen.getByRole('option', { name: /alpha run/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /gamma run/i })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /beta run/i })).not.toBeInTheDocument();
  });

  it('clicking a row selects it, closes the list, and shows its label closed', async () => {
    const user = userEvent.setup();
    render(<ControlledSelect id="f" label="Run" options={OPTIONS} placeholder="Choose…" />);

    const field = screen.getByLabelText('Run');
    await user.click(field);
    await user.click(screen.getByRole('option', { name: /beta run/i }));

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(field).toHaveTextContent('Beta run');
  });

  it('keyboard: ArrowDown highlights, Enter selects, Escape closes without selecting', async () => {
    const user = userEvent.setup();
    render(<ControlledSelect id="f" label="Run" options={OPTIONS} placeholder="Choose…" />);

    const field = screen.getByLabelText('Run');
    await user.click(field);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(field).toHaveTextContent('Choose…');

    await user.click(field);
    await user.keyboard('{ArrowDown}{Enter}');

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    // Second option (index 1) after one ArrowDown from index 0.
    expect(field).toHaveTextContent('Beta run');
  });

  it('a clear button appears once a value is set and emptyLabel is given, and resets to empty', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SearchableSelect
        id="f"
        label="Run"
        value="2"
        onChange={onChange}
        options={OPTIONS}
        emptyLabel="All runs"
      />
    );

    const clearButton = screen.getByRole('button', { name: /clear run/i });
    await user.click(clearButton);
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('shows emptyLabel, not a clear button, once nothing is selected', () => {
    render(
      <SearchableSelect id="f" label="Run" value="" onChange={() => {}} options={OPTIONS} emptyLabel="All runs" />
    );

    expect(screen.getByLabelText('Run')).toHaveTextContent('All runs');
    expect(screen.queryByRole('button', { name: /clear/i })).not.toBeInTheDocument();
  });

  it('clicking outside the field closes the list without selecting anything', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <SearchableSelect id="f" label="Run" value="" onChange={() => {}} options={OPTIONS} placeholder="Choose…" />
        <button type="button">Outside</button>
      </div>
    );

    await user.click(screen.getByLabelText('Run'));
    expect(screen.getByRole('listbox')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('a disabled field never opens', async () => {
    const user = userEvent.setup();
    render(
      <SearchableSelect id="f" label="Run" value="" onChange={() => {}} options={OPTIONS} placeholder="Choose…" disabled />
    );

    const field = screen.getByLabelText('Run');
    expect(field).toBeDisabled();
    await user.click(field);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  describe('grouped/versioned options', () => {
    // Mirrors what pages/ExperimentBuilder.jsx's `groupTemplatesByName`
    // builds: a name with one version stays a plain leaf; a name with
    // several becomes a group whose own value is the resolved (active)
    // version, with each version as a child.
    const GROUPED_OPTIONS = [
      { value: '10', label: 'default-generation-variant-a (v1)' },
      {
        value: '23',
        label: 'gen-a',
        closedLabel: 'gen-a (v3)',
        children: [
          { value: '23', label: 'v3 · active', closedLabel: 'gen-a (v3)' },
          { value: '22', label: 'v2', closedLabel: 'gen-a (v2)' },
          { value: '21', label: 'v1', closedLabel: 'gen-a (v1)' },
        ],
      },
    ];

    it('a single-version group renders as a plain row with no toggle', async () => {
      const user = userEvent.setup();
      render(
        <SearchableSelect id="f" label="Prompt" value="" onChange={() => {}} options={GROUPED_OPTIONS} placeholder="Choose…" />
      );

      await user.click(screen.getByLabelText('Prompt'));
      const leaf = screen.getByTestId('f-option-10');
      expect(within(leaf).queryByRole('button')).not.toBeInTheDocument();
      // Its versions stay hidden until the toggle is used.
      expect(screen.queryByTestId('f-version-22')).not.toBeInTheDocument();
    });

    it("clicking a multi-version group's row selects its resolved (active) version, not just its name", async () => {
      const user = userEvent.setup();
      render(<ControlledSelect id="f" label="Prompt" options={GROUPED_OPTIONS} placeholder="Choose…" />);

      const field = screen.getByLabelText('Prompt');
      await user.click(field);
      await user.click(screen.getByTestId('f-option-23'));

      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
      expect(field).toHaveTextContent('gen-a (v3)');
    });

    it('the toggle expands version rows without selecting, each pickable on its own', async () => {
      const user = userEvent.setup();
      render(<ControlledSelect id="f" label="Prompt" options={GROUPED_OPTIONS} placeholder="Choose…" />);

      await user.click(screen.getByLabelText('Prompt'));
      expect(screen.queryByTestId('f-version-22')).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /expand gen-a versions/i }));
      expect(screen.getByTestId('f-version-22')).toHaveTextContent('v2');
      // The toggle itself never selects - the list stays open. The active
      // version (v3) now also has its own row, sharing the group's value.
      expect(screen.getByRole('listbox')).toBeInTheDocument();
      expect(screen.getByTestId('f-version-23')).toHaveTextContent('v3');

      await user.click(screen.getByTestId('f-version-22'));
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Prompt')).toHaveTextContent('gen-a (v2)');
    });

    it('searching a version number narrows to the matching version and auto-expands its group', async () => {
      const user = userEvent.setup();
      render(
        <SearchableSelect id="f" label="Prompt" value="" onChange={() => {}} options={GROUPED_OPTIONS} placeholder="Choose…" />
      );

      await user.click(screen.getByLabelText('Prompt'));
      await user.type(screen.getByRole('searchbox'), 'v2');

      expect(screen.getByTestId('f-version-22')).toBeInTheDocument();
      expect(screen.queryByTestId('f-version-23')).not.toBeInTheDocument();
      expect(screen.queryByTestId('f-version-21')).not.toBeInTheDocument();
      // The single-version leaf never mentions "v2" at all, so it drops out.
      expect(screen.queryByTestId('f-option-10')).not.toBeInTheDocument();
    });

    it('keyboard: ArrowRight expands the highlighted group, ArrowLeft collapses it', async () => {
      const user = userEvent.setup();
      render(
        <SearchableSelect id="f" label="Prompt" value="" onChange={() => {}} options={GROUPED_OPTIONS} placeholder="Choose…" />
      );

      const field = screen.getByLabelText('Prompt');
      await user.click(field);
      // Highlighted index starts at 0 (the single-version leaf) - move down
      // once to reach the "gen-a" group row.
      await user.keyboard('{ArrowDown}');
      expect(screen.queryByTestId('f-version-22')).not.toBeInTheDocument();

      await user.keyboard('{ArrowRight}');
      expect(screen.getByTestId('f-version-22')).toBeInTheDocument();

      await user.keyboard('{ArrowLeft}');
      expect(screen.queryByTestId('f-version-22')).not.toBeInTheDocument();
    });
  });
});
