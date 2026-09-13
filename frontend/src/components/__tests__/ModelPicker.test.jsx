import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ModelPicker from '../ModelPicker';

const MODELS = [
  {
    id: 'anthropic/claude-sonnet-4',
    name: 'Anthropic: Claude Sonnet 4',
    pricing: { prompt: '0.000003', completion: '0.000015' },
    context_length: 200000,
  },
  {
    id: 'openai/gpt-4o-mini',
    name: 'GPT-4o mini',
    pricing: { prompt: '0.0000002', completion: '0.0000006' },
    context_length: 128000,
  },
  {
    id: 'meta/llama-free',
    name: 'Llama Free',
    pricing: { prompt: '0', completion: '0' },
    context_length: 8192,
  },
  {
    id: 'mystery/unknown-price',
    name: 'Mystery Model',
    pricing: { prompt: '-1', completion: '-1' },
  },
];

const successQuery = { isSuccess: true, isError: false, isLoading: false, data: { results: MODELS } };
const errorQuery = { isSuccess: false, isError: true, isLoading: false, data: undefined };

// A tiny controlled wrapper so tests can assert what onChange was called
// with AND see the resulting `value` reflected back into the picker, the
// same round trip a real form does.
function ControlledPicker(props) {
  const [value, setValue] = useState(props.value ?? '');
  return <ModelPicker {...props} value={value} onChange={setValue} />;
}

describe('ModelPicker', () => {
  it('shows "Select a model…" when nothing is chosen, and opens the list on click', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    const field = screen.getByLabelText('Model');
    expect(field).toHaveAttribute('placeholder', 'Select a model…');

    await user.click(field);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByTestId('model-option-openai/gpt-4o-mini')).toBeInTheDocument();
  });

  it('filters by name or id, case-insensitively, requiring every typed word to match', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    const field = screen.getByLabelText('Model');
    await user.click(field);
    await user.type(field, 'claude SONNET');

    expect(screen.getByTestId('model-option-anthropic/claude-sonnet-4')).toBeInTheDocument();
    expect(screen.queryByTestId('model-option-openai/gpt-4o-mini')).not.toBeInTheDocument();
  });

  it('clicking a row selects it, closes the list, and shows its price and OpenRouter link', async () => {
    const user = userEvent.setup();
    render(<ControlledPicker id="m" label="Model" modelsQuery={successQuery} />);

    const field = screen.getByLabelText('Model');
    await user.click(field);
    await user.click(screen.getByTestId('model-option-anthropic/claude-sonnet-4'));

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(field).toHaveValue('Anthropic: Claude Sonnet 4 (anthropic/claude-sonnet-4)');
    expect(screen.getByText(/in \$3\.00/i)).toBeInTheDocument();
    expect(screen.getByText(/out \$15\.00/i)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /openrouter/i });
    expect(link).toHaveAttribute('href', 'https://openrouter.ai/anthropic/claude-sonnet-4');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
  });

  it('clicking the OpenRouter link inside a row does not select that row', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={onChange} modelsQuery={successQuery} />);

    await user.click(screen.getByLabelText('Model'));
    const row = screen.getByTestId('model-option-openai/gpt-4o-mini');
    await user.click(within(row).getByRole('link', { name: /openrouter/i }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('formats free and variable pricing distinctly from a real price', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    await user.click(screen.getByLabelText('Model'));
    expect(within(screen.getByTestId('model-option-meta/llama-free')).getByText(/in free/i)).toBeInTheDocument();
    expect(
      within(screen.getByTestId('model-option-mystery/unknown-price')).getByText(/in varies/i)
    ).toBeInTheDocument();
  });

  it('the provider filter narrows the list to one provider', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    await user.click(screen.getByLabelText('Model'));
    await user.selectOptions(screen.getByLabelText('Model provider filter'), 'openai');

    expect(screen.getByTestId('model-option-openai/gpt-4o-mini')).toBeInTheDocument();
    expect(screen.queryByTestId('model-option-anthropic/claude-sonnet-4')).not.toBeInTheDocument();
  });

  it('"Free only" keeps only zero-priced models', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    await user.click(screen.getByLabelText('Model'));
    await user.click(screen.getByLabelText(/free only/i));

    expect(screen.getByTestId('model-option-meta/llama-free')).toBeInTheDocument();
    expect(screen.queryByTestId('model-option-openai/gpt-4o-mini')).not.toBeInTheDocument();
  });

  it('sorting by input price puts the cheapest real price first and unknown pricing last', async () => {
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={successQuery} />);

    await user.click(screen.getByLabelText('Model'));
    await user.selectOptions(screen.getByLabelText('Model sort'), 'input-price-asc');

    const options = within(screen.getByRole('listbox'))
      .getAllByRole('option')
      .map((option) => option.dataset.testid);
    // free (0) < gpt-4o-mini (0.0000002) < claude sonnet 4 (0.000003) < unknown (variable, sorts last)
    expect(options).toEqual([
      'model-option-meta/llama-free',
      'model-option-openai/gpt-4o-mini',
      'model-option-anthropic/claude-sonnet-4',
      'model-option-mystery/unknown-price',
    ]);
  });

  it('keyboard: ArrowDown highlights, Enter selects, Escape closes without selecting', async () => {
    const user = userEvent.setup();
    render(<ControlledPicker id="m" label="Model" modelsQuery={successQuery} />);

    const field = screen.getByLabelText('Model');
    await user.click(field);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(field).toHaveValue('');

    await user.click(field);
    await user.keyboard('{ArrowDown}{Enter}');

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    // Second option in the default (name-sorted) list: Anthropic (index 0),
    // then GPT-4o mini (index 1) after one ArrowDown.
    expect(field).toHaveValue('GPT-4o mini (openai/gpt-4o-mini)');
  });

  it('caps rendering at the first 100 matches and shows a refine-your-search note', async () => {
    const manyModels = Array.from({ length: 150 }, (_, index) => ({
      id: `provider/model-${index}`,
      name: `Model ${index}`,
      pricing: { prompt: '0.000001', completion: '0.000002' },
    }));
    const query = { isSuccess: true, isError: false, isLoading: false, data: { results: manyModels } };
    const user = userEvent.setup();
    render(<ModelPicker id="m" label="Model" value="" onChange={() => {}} modelsQuery={query} />);

    await user.click(screen.getByLabelText('Model'));
    expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(100);
    expect(screen.getByText(/showing the first 100 matches/i)).toBeInTheDocument();
  });

  it('falls back to a plain text input when the catalogue failed to load', async () => {
    const user = userEvent.setup();
    render(<ControlledPicker id="m" label="Model" modelsQuery={errorQuery} />);

    expect(screen.getByText(/model catalogue unavailable/i)).toBeInTheDocument();
    const field = screen.getByLabelText('Model');
    await user.type(field, 'custom/model-id');
    expect(field).toHaveValue('custom/model-id');
    // No combobox affordances in the fallback state.
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('shows a field error next to the picker', () => {
    render(
      <ModelPicker
        id="m"
        label="Model"
        value=""
        onChange={() => {}}
        modelsQuery={successQuery}
        fieldError="This field is required."
      />
    );

    expect(screen.getByText('This field is required.')).toBeInTheDocument();
  });
});
