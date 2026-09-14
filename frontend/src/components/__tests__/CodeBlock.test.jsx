import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CodeBlock, { formatJsonLike } from '../CodeBlock';

describe('formatJsonLike', () => {
  it('indents compact JSON, fenced JSON, and JSON with raw line breaks inside strings', () => {
    expect(formatJsonLike('{"a":{"b":1}}')).toBe('{\n  "a": {\n    "b": 1\n  }\n}');
    expect(formatJsonLike('```json\n{"a":1}\n```')).toBe('{\n  "a": 1\n}');
    expect(formatJsonLike('{"body":"line one\nline two"}')).toBe('{\n  "body": "line one\\nline two"\n}');
  });

  it('leaves text that is not a JSON object or array alone', () => {
    expect(formatJsonLike('plain prompt text')).toBeNull();
    expect(formatJsonLike('{"cut off": ')).toBeNull();
    expect(formatJsonLike('42')).toBeNull();
  });
});

describe('CodeBlock', () => {
  it('shows JSON formatted by default and can switch to the text as received', async () => {
    const user = userEvent.setup();
    render(<CodeBlock title="Raw response" code='{"a":1}' />);
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /show as received/i }));
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
  });
});
