import { File as NodeFile } from 'node:buffer';
import { FormData as UndiciFormData } from 'undici';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';

// jsdom's own `File`/`FormData` classes do not survive the trip through
// msw's Node-side request interception intact: a file appended via jsdom's
// FormData/File arrives at the handler with its name coerced to "blob" and
// its content coerced to the literal string "undefined" (confirmed by
// direct reproduction, independent of React, against this exact
// axios+msw+jsdom combination - a real interop gap between jsdom's realm
// and the undici realm msw's Node interceptors reconstruct the request in,
// not a bug in Resources.jsx, which just calls `new FormData()` and lets
// the runtime own it - fine in a real browser). Node's OWN `File` (from
// `node:buffer`) together with undici's OWN `FormData` (the same low-level
// library jsdom itself uses for networking, and already an actual
// dependency of jsdom - see package.json) round-trip correctly, so tests
// that upload a file use those instead of the ambient jsdom globals.
function stubFormDataForFileUploads() {
  vi.stubGlobal('FormData', UndiciFormData);
}

function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

// Mirrors corpus/serializers.py's ResourceFileSerializer/
// FewShotExampleSerializer field shapes - not an invented shape. `text` is
// what the real backend decodes an uploaded file into
// (ResourceFileSerializer.validate); `resolved_input`/`resolved_output` are
// read-only, server-computed at request time from whichever side is
// file-backed (FewShotExample.resolved_input/resolved_output).
let resources;
let fewshots;
let nextResourceId;
let nextFewShotId;

function resetFixtures() {
  resources = [
    { id: 1, name: 'hcv-table', kind: 'taxonomy', file: null, text: 'HCV-01: ...', description: '', uploaded_by: 1, created_at: '2026-01-01T00:00:00Z' },
    { id: 2, name: 'oldsmar-system', kind: 'system_description', file: null, text: 'A water treatment plant serving Oldsmar.', description: '', uploaded_by: 1, created_at: '2026-01-01T00:00:00Z' },
    { id: 3, name: 'oldsmar-scenarios', kind: 'scenarios', file: null, text: 'sc_01: an operator notices...', description: '', uploaded_by: 1, created_at: '2026-01-01T00:00:00Z' },
  ];
  fewshots = [];
  nextResourceId = 4;
  nextFewShotId = 1;
}

function resourceById(id) {
  return resources.find((resource) => resource.id === Number(id));
}

function resolveSide(fewshot, side) {
  const fileId = fewshot[`${side}_file`];
  if (fileId != null) return resourceById(fileId)?.text ?? '';
  return fewshot[`${side}_text`] ?? '';
}

function toFewShotResponse(fewshot) {
  return {
    ...fewshot,
    resolved_input: resolveSide(fewshot, 'input'),
    resolved_output: resolveSide(fewshot, 'output'),
  };
}

beforeEach(() => {
  authState.currentUserEmail = 'researcher@example.com';
  resetFixtures();

  server.use(
    http.get('/api/resources/', () => HttpResponse.json(paginated(resources))),

    http.post('/api/resources/', async ({ request }) => {
      const formData = await request.formData();
      const file = formData.get('file');
      const text = file && typeof file !== 'string' ? await file.text() : formData.get('text') || '';
      const created = {
        id: nextResourceId++,
        name: formData.get('name'),
        kind: formData.get('kind') || 'other',
        file: file && typeof file !== 'string' ? `/media/resources/${file.name}` : null,
        text,
        description: formData.get('description') || '',
        uploaded_by: 1,
        created_at: '2026-09-10T00:00:00Z',
      };
      resources.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),

    http.delete('/api/resources/:id/', ({ params }) => {
      const id = Number(params.id);
      const referencing = fewshots.find(
        (fewshot) => fewshot.input_file === id || fewshot.output_file === id
      );
      if (referencing) {
        const resource = resourceById(id);
        // The real body: corpus.views.ResourceFileViewSet.perform_destroy
        // raises a plain-string ValidationError, which DRF returns as a
        // BARE ARRAY, not {"detail": ...} - see api/errors.js.
        return HttpResponse.json(
          [
            `'${resource.name}' is still referenced by: ${referencing.name}. Remove or repoint those first.`,
          ],
          { status: 400 }
        );
      }
      resources = resources.filter((resource) => resource.id !== id);
      return new HttpResponse(null, { status: 204 });
    }),

    http.get('/api/fewshots/', () => HttpResponse.json(paginated(fewshots.map(toFewShotResponse)))),

    http.post('/api/fewshots/', async ({ request }) => {
      const body = await request.json();
      const created = {
        id: nextFewShotId++,
        phase: body.phase,
        name: body.name,
        input_text: body.input_text || '',
        input_file: body.input_file ?? null,
        output_text: body.output_text || '',
        output_file: body.output_file ?? null,
        order: 0,
        is_active: true,
      };
      fewshots.push(created);
      return HttpResponse.json(toFewShotResponse(created), { status: 201 });
    })
  );
});

// Resource names double as <option> text in the few-shot pairing form's two
// selects, so a plain screen.getByText('oldsmar-system') is ambiguous the
// moment that form is on the page (which is always, on this screen). Every
// resource-row lookup below is scoped to the "Resources" list itself; every
// few-shot-row lookup to the "Few-shot examples" list - see the
// aria-label on each <ul> in Resources.jsx. Both are `find`-based (async):
// the list itself only exists once the initial GET has resolved, so a
// caller must await it before using it, even on the very first call.
async function resourceList() {
  return screen.findByRole('list', { name: /^resources$/i });
}

async function fewshotList() {
  return screen.findByRole('list', { name: /^few-shot examples$/i });
}

describe('Resources', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uploads a text file and shows its extracted text', async () => {
    stubFormDataForFileUploads();
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('hcv-table');

    await user.type(screen.getByLabelText(/^name$/i, { selector: '#resource-name' }), 'incident-report');
    await user.selectOptions(screen.getByLabelText(/^kind$/i), 'other');
    const file = new NodeFile(['Full incident write-up, verbatim.'], 'incident.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText(/file/i), file);
    await user.click(screen.getByRole('button', { name: /^upload$/i }));

    expect(await within(resourceListEl).findByText('incident-report')).toBeInTheDocument();
    // Unique across the page - only the extracted-text <pre> carries it.
    expect(await screen.findByText('Full incident write-up, verbatim.')).toBeInTheDocument();
  });

  it('visibly marks a taxonomy-kind resource with a note that it will be refused for variant A', async () => {
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('hcv-table');
    const row = within(resourceListEl).getByText('hcv-table').closest('li');
    // The kind badge itself (exact match - distinct from the longer warning
    // sentence below, which also contains the word "taxonomy").
    expect(within(row).getByText('Taxonomy')).toBeInTheDocument();
    expect(within(row).getByRole('note')).toHaveTextContent(/refused/i);
    expect(within(row).getByRole('note')).toHaveTextContent(/variant a/i);

    // A non-taxonomy resource gets no such warning.
    const otherRow = within(resourceListEl).getByText('oldsmar-system').closest('li');
    expect(within(otherRow).queryByRole('note')).not.toBeInTheDocument();
  });

  it('pairs an input and an output resource into a few-shot, with the phase chosen', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('oldsmar-system');
    // No few-shot exists yet, so the "Few-shot examples" list itself isn't
    // rendered (an EmptyState stands in for it) - only await it AFTER
    // pairing creates the first one.

    await user.type(screen.getByLabelText(/^name$/i, { selector: '#fewshot-name' }), 'oldsmar-pair');
    await user.selectOptions(screen.getByLabelText(/^phase$/i), '3');
    await user.selectOptions(screen.getByLabelText(/input resource/i), 'oldsmar-system');
    await user.selectOptions(screen.getByLabelText(/output resource/i), 'oldsmar-scenarios');
    await user.click(screen.getByRole('button', { name: /pair few-shot/i }));

    const fewshotListEl = await fewshotList();
    expect(await within(fewshotListEl).findByText('oldsmar-pair')).toBeInTheDocument();
    expect(within(fewshotListEl).getByText(/phase 3/i)).toBeInTheDocument();
    expect(fewshots).toHaveLength(1);
    expect(fewshots[0]).toMatchObject({ phase: 3, name: 'oldsmar-pair', input_file: 2, output_file: 3 });
  });

  it("shows a few-shot's resolved input/output preview exactly as the API returns it", async () => {
    fewshots.push({
      id: 99,
      phase: 2,
      name: 'seeded-pair',
      input_text: 'Inline system prompt for testing.',
      input_file: null,
      output_text: '',
      output_file: 3,
      order: 0,
      is_active: true,
    });

    renderApp({ initialEntries: ['/resources'] });

    const fewshotListEl = await fewshotList();
    await within(fewshotListEl).findByText('seeded-pair');
    // Input side is inline text; output side resolves through the file -
    // both must come from the server's resolved_input/resolved_output, not
    // be recomputed client-side from input_text/input_file. The output text
    // happens to equal the underlying resource's own extracted text
    // (resolved_output IS that text) - scoped to the few-shot list, since
    // the resource list shows the identical string too, by design.
    expect(within(fewshotListEl).getByText('Inline system prompt for testing.')).toBeInTheDocument();
    expect(within(fewshotListEl).getByText('sc_01: an operator notices...')).toBeInTheDocument();
  });

  it('shows the API refusal, not a crash, when deleting a resource a few-shot references', async () => {
    fewshots.push({
      id: 1,
      phase: 2,
      name: 'oldsmar',
      input_text: 'system',
      input_file: null,
      output_text: '',
      output_file: 3,
      order: 0,
      is_active: true,
    });
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('oldsmar-scenarios');
    const row = within(resourceListEl).getByText('oldsmar-scenarios').closest('li');
    await user.click(within(row).getByRole('button', { name: /delete/i }));

    expect(await within(row).findByRole('alert')).toHaveTextContent(/still referenced by/i);
    expect(await within(row).findByRole('alert')).toHaveTextContent(/oldsmar/i);
    // Not removed, and the rest of the page is still intact - no crash.
    expect(within(resourceListEl).getByText('oldsmar-scenarios')).toBeInTheDocument();
    expect(within(resourceListEl).getByText('hcv-table')).toBeInTheDocument();
  });
});
