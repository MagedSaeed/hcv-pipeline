import { File as NodeFile } from 'node:buffer';
import { FormData as UndiciFormData } from 'undici';
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, requireCsrf } from '../../test/mockApi';

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

    // Coordinator review (final polish, item 4): none of this file's write
    // handlers enforced CSRF before now - every OTHER screen's handlers do
    // (see Corpus.test.jsx/PromptEditor.test.jsx), but these were left as
    // one of the last permissive spots, so a client regression that stopped
    // sending `X-CSRFToken` on a resource/few-shot write would still pass
    // every test in this file. `/api/resources/` and `/api/fewshots/` carry
    // no extra staff restriction (spec §9's CRUD routes for both), so only
    // `requireCsrf` applies here - never `requireStaff`.
    http.post('/api/resources/', async ({ request }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
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

    http.patch('/api/resources/:id/', async ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const resource = resourceById(params.id);
      const body = await request.json();
      Object.assign(resource, body);
      return HttpResponse.json(resource);
    }),

    http.delete('/api/resources/:id/', ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
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
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
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
    }),

    http.patch('/api/fewshots/:id/', async ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const fewshot = fewshots.find((item) => item.id === id);
      const body = await request.json();
      Object.assign(fewshot, body);
      return HttpResponse.json(toFewShotResponse(fewshot));
    }),

    http.delete('/api/fewshots/:id/', ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      fewshots = fewshots.filter((item) => item.id !== id);
      return new HttpResponse(null, { status: 204 });
    })
  );
});

// Resource names double as <option> text in the few-shot pairing form's two
// selects, so a plain screen.getByText('oldsmar-system') is ambiguous the
// moment that form is open. Every resource-row lookup below is scoped to the
// "Resources" list itself - see the aria-label on that <ul> in
// Resources.jsx. It is `find`-based (async): the list itself only exists
// once the initial GET has resolved, so a caller must await it before using
// it, even on the very first call.
async function resourceList() {
  return screen.findByRole('list', { name: /^resources$/i });
}

// Few-shots are grouped under a per-phase <ul aria-label="Phase N — ..."> -
// one list per phase, not one shared "Few-shot examples" list - so a
// caller names which phase's group it wants.
async function fewshotGroup(phaseLabel) {
  return screen.findByRole('list', { name: phaseLabel });
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

    await user.click(screen.getByRole('button', { name: /^add resource$/i }));
    await user.type(screen.getByLabelText(/^name$/i), 'incident-report');
    await user.selectOptions(screen.getByLabelText(/^kind$/i), 'other');
    const file = new NodeFile(['Full incident write-up, verbatim.'], 'incident.txt', { type: 'text/plain' });
    // Not /file/i - that also matches the "Upload file" radio label.
    await user.upload(screen.getByLabelText(/^file /i), file);
    await user.click(screen.getByRole('button', { name: /^upload$/i }));

    expect(await within(resourceListEl).findByText('incident-report')).toBeInTheDocument();
    // Unique across the page - only the extracted-text <pre> carries it.
    expect(await screen.findByText('Full incident write-up, verbatim.')).toBeInTheDocument();
  });

  it('pastes text instead of uploading a file when adding a resource', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('hcv-table');

    await user.click(screen.getByRole('button', { name: /^add resource$/i }));
    await user.type(screen.getByLabelText(/^name$/i), 'pasted-vocab');
    await user.click(screen.getByRole('radio', { name: /paste text/i }));
    await user.type(screen.getByLabelText(/^text$/i), 'Term: definition.');
    await user.click(screen.getByRole('button', { name: /^upload$/i }));

    expect(await within(resourceListEl).findByText('pasted-vocab')).toBeInTheDocument();
    expect(resources.find((resource) => resource.name === 'pasted-vocab')).toMatchObject({
      text: 'Term: definition.',
      file: null,
    });
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

  it('edits a resource through the Edit modal', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    await within(resourceListEl).findByText('oldsmar-system');
    const row = within(resourceListEl).getByText('oldsmar-system').closest('li');
    await user.click(within(row).getByRole('button', { name: /^edit$/i }));

    const nameField = screen.getByLabelText(/^name$/i);
    await user.clear(nameField);
    await user.type(nameField, 'oldsmar-system-renamed');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await within(resourceListEl).findByText('oldsmar-system-renamed')).toBeInTheDocument();
    expect(resources.find((resource) => resource.id === 2).name).toBe('oldsmar-system-renamed');
  });

  it('pairs an input and an output resource into a few-shot, with the phase chosen', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=fewshots'] });

    // Switch to the resources tab isn't needed - the pairing form's selects
    // read from the same `useResources` data regardless of which tab is
    // active. `findByRole` (not `getByRole`): the app shell itself renders
    // only once the auth check (`/api/auth/me/`) resolves, so the very
    // first query after a fresh render must be one that awaits.
    await user.click(await screen.findByRole('button', { name: /^new few-shot$/i }));
    await user.type(screen.getByLabelText(/^name$/i), 'oldsmar-pair');
    await user.selectOptions(screen.getByLabelText(/^phase$/i), '3');
    const inputSelect = screen.getByLabelText(/input resource/i);
    await within(inputSelect).findByText('oldsmar-system'); // wait for useResources to resolve
    await user.selectOptions(inputSelect, 'oldsmar-system');
    const outputSelect = screen.getByLabelText(/output resource/i);
    await user.selectOptions(outputSelect, 'oldsmar-scenarios');
    await user.click(screen.getByRole('button', { name: /pair few-shot/i }));

    const phase3List = await fewshotGroup(/phase 3 — labelling/i);
    expect(await within(phase3List).findByText('oldsmar-pair')).toBeInTheDocument();
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

    renderApp({ initialEntries: ['/resources?tab=fewshots'] });

    const phase2List = await fewshotGroup(/phase 2 — generation/i);
    await within(phase2List).findByText('seeded-pair');
    // Input side is inline text; output side resolves through the file -
    // both must come from the server's resolved_input/resolved_output, not
    // be recomputed client-side from input_text/input_file.
    expect(within(phase2List).getByText('Inline system prompt for testing.')).toBeInTheDocument();
    expect(within(phase2List).getByText('sc_01: an operator notices...')).toBeInTheDocument();
  });

  it('edits a few-shot through the Edit modal', async () => {
    fewshots.push({
      id: 5,
      phase: 2,
      name: 'to-rename',
      input_text: 'in',
      input_file: null,
      output_text: 'out',
      output_file: null,
      order: 0,
      is_active: true,
    });
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=fewshots'] });

    const phase2List = await fewshotGroup(/phase 2 — generation/i);
    const card = within(phase2List).getByText('to-rename').closest('li');
    await user.click(within(card).getByRole('button', { name: /^edit$/i }));

    const nameField = screen.getByLabelText(/^name$/i);
    await user.clear(nameField);
    await user.type(nameField, 'renamed-pair');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await within(phase2List).findByText('renamed-pair')).toBeInTheDocument();
    expect(fewshots.find((item) => item.id === 5).name).toBe('renamed-pair');
  });

  it('deletes a few-shot after confirming', async () => {
    fewshots.push({
      id: 6,
      phase: 3,
      name: 'to-delete',
      input_text: 'in',
      input_file: null,
      output_text: 'out',
      output_file: null,
      order: 0,
      is_active: true,
    });
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=fewshots'] });

    const phase3List = await fewshotGroup(/phase 3 — labelling/i);
    const card = within(phase3List).getByText('to-delete').closest('li');
    await user.click(within(card).getByRole('button', { name: /^delete$/i }));
    await user.click(within(card).getByRole('button', { name: /^confirm$/i }));

    expect(screen.queryByText('to-delete')).not.toBeInTheDocument();
    expect(fewshots.find((item) => item.id === 6)).toBeUndefined();
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
    await user.click(within(row).getByRole('button', { name: /^delete$/i }));
    await user.click(within(row).getByRole('button', { name: /^confirm$/i }));

    expect(await within(row).findByRole('alert')).toHaveTextContent(/still referenced by/i);
    expect(await within(row).findByRole('alert')).toHaveTextContent(/oldsmar/i);
    // Not removed, and the rest of the page is still intact - no crash.
    expect(within(resourceListEl).getByText('oldsmar-scenarios')).toBeInTheDocument();
    expect(within(resourceListEl).getByText('hcv-table')).toBeInTheDocument();
  });

  it('switches between the Text resources and Few-shot examples tabs, keeping the choice in the URL', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources'] });

    await resourceList();
    expect(screen.getByRole('button', { name: /^add resource$/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /few-shot examples/i }));
    expect(screen.getByRole('button', { name: /^new few-shot$/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^add resource$/i })).not.toBeInTheDocument();
  });

  it('fetches every page of the resources list, not just the first 50 (fails if only page 1 is read)', async () => {
    // 55 resources - task 10.2-10.4: task 10.1's own "a modest, hand-
    // curated set, stays first-page" exception for `useResources` was
    // rejected - the rule has no size-based exceptions left.
    const fillerResources = Array.from({ length: 55 }, (_, index) => ({
      id: 100 + index,
      name: `filler-resource-${index + 1}`,
      kind: 'other',
      file: null,
      text: '',
      description: '',
      uploaded_by: 1,
      created_at: '2026-01-01T00:00:00Z',
    }));
    server.use(http.get('/api/resources/', ({ request }) => HttpResponse.json(paginate(fillerResources, request))));

    renderApp({ initialEntries: ['/resources'] });

    const resourceListEl = await resourceList();
    expect(await within(resourceListEl).findByText('filler-resource-55')).toBeInTheDocument();
  });

  it('fetches every page of the few-shots list, not just the first 50 (fails if only page 1 is read)', async () => {
    // 55 few-shots - same reasoning as the resources test above, for
    // `useFewShots`.
    const fillerFewshots = Array.from({ length: 55 }, (_, index) => ({
      id: 200 + index,
      phase: 2,
      name: `filler-fewshot-${index + 1}`,
      input_text: 'in',
      input_file: null,
      output_text: 'out',
      output_file: null,
      resolved_input: 'in',
      resolved_output: 'out',
      order: index + 1,
      is_active: true,
    }));
    server.use(http.get('/api/fewshots/', ({ request }) => HttpResponse.json(paginate(fillerFewshots, request))));

    renderApp({ initialEntries: ['/resources?tab=fewshots'] });

    const phase2List = await fewshotGroup(/phase 2 — generation/i);
    expect(await within(phase2List).findByText('filler-fewshot-55')).toBeInTheDocument();
  });

  // Coordinator review (final polish, item 4): proves the CSRF guard just
  // wired into this file's own handlers actually refuses a write with no
  // `X-CSRFToken` header - not merely present in the file, unreachable. A
  // fresh axios instance (no interceptors at all, unlike `apiClient`, which
  // always attaches the header correctly and so could never exercise this)
  // talks to the SAME mock server msw intercepts regardless of which client
  // made the request - the same "call the raw client directly" pattern
  // ExperimentBuilder.test.jsx/ExperimentDetail.test.jsx use to prove their
  // own staff guards are wired, applied here to CSRF instead.
  it('the mock refuses a write to /api/fewshots/ with no CSRF header (guard actually wired)', async () => {
    const rawClient = axios.create({ baseURL: '/api', withCredentials: true });

    await expect(rawClient.post('/fewshots/', { phase: 2, name: 'no-csrf-header' })).rejects.toMatchObject({
      response: { status: 403 },
    });
    // Nothing was created - the refusal happened before the handler's own
    // body ever ran.
    expect(fewshots).toHaveLength(0);
  });
});
