import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCreateAnalysis, useStartNew, useUpdateAnalysis } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../../components/ErrorBanner';
import { linkButton, primaryButton, secondaryButton, stepTitle, textInput } from '../../components/analyses/styles';
import { EXAMPLE_SYSTEM, OUTLINE } from './exampleSystem';

const AUTOSAVE_DELAY_MS = 800;
const DESCRIPTION_MAX_LENGTH = 50000;
const QUESTION = 'What system would you like to examine?';

function changedFields(draft, saved) {
  return Object.fromEntries(Object.entries(draft).filter(([field, value]) => saved[field] !== value));
}

function ReadOnlySystem({ analysis }) {
  const navigate = useNavigate();
  const { canRunAnalyses } = useAuth();
  const startNew = useStartNew();

  return (
    <section className="space-y-5">
      <h2 className={stepTitle}>System</h2>
      <p
        dir="auto"
        className="max-h-[calc(100dvh-28rem)] min-h-[10rem] overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-gray-200 bg-white p-5 text-sm leading-relaxed text-gray-800"
      >
        {analysis.description}
      </p>
      {analysis.locked && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600">
          <p>
            Locked because scenarios were generated from it.
            {canRunAnalyses && ' To change it, start a new analysis from it.'}
          </p>
          {canRunAnalyses && (
            <button
              type="button"
              disabled={startNew.isPending}
              onClick={() =>
                startNew.mutate(analysis.id, { onSuccess: (copy) => navigate(`/analyses/${copy.id}/system`) })
              }
              className={secondaryButton}
            >
              Start new from this description
            </button>
          )}
        </div>
      )}
      {startNew.isError && <ErrorBanner error={startNew.error} />}
    </section>
  );
}

// Saves the draft after a pause, on blur and on leaving, one request at a time.
function useAutosave(analysis) {
  const navigate = useNavigate();
  const { mutateAsync: create } = useCreateAnalysis();
  const { mutateAsync: update } = useUpdateAnalysis();
  const initial = { name: analysis?.name ?? '', description: analysis?.description ?? '' };
  const [draft, setDraft] = useState(initial);
  const [status, setStatus] = useState({ phase: 'idle', error: null });
  const id = useRef(analysis?.id ?? null);
  const latest = useRef(initial);
  const saved = useRef(initial);
  const queue = useRef(Promise.resolve());
  const timer = useRef(null);
  const mounted = useRef(false);

  // Resolves to the analysis id, or null when there is nothing to keep or the save failed.
  const save = useCallback(() => {
    clearTimeout(timer.current);
    queue.current = queue.current.then(async () => {
      const body = { ...latest.current };
      const changes = changedFields(body, saved.current);
      if (Object.keys(changes).length === 0) return id.current;
      if (!id.current && !body.name.trim() && !body.description.trim()) return null;
      if (mounted.current) setStatus({ phase: 'saving', error: null });
      try {
        if (id.current) {
          await update({ id: id.current, ...changes });
        } else {
          id.current = (await create(body)).id;
          if (mounted.current) navigate(`/analyses/${id.current}/system`, { replace: true, state: { session: 'new' } });
        }
        saved.current = body;
        if (mounted.current) setStatus({ phase: 'saved', error: null });
        return id.current;
      } catch (error) {
        if (mounted.current) setStatus({ phase: 'failed', error });
        return null;
      }
    });
    return queue.current;
  }, [create, update, navigate]);

  // Navigation changes `navigate`, so leaving reads the newest save through a ref.
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  useEffect(() => {
    mounted.current = true;
    function warnIfUnsaved(event) {
      if (Object.keys(changedFields(latest.current, saved.current)).length) event.preventDefault();
    }
    window.addEventListener('beforeunload', warnIfUnsaved);
    return () => {
      window.removeEventListener('beforeunload', warnIfUnsaved);
      mounted.current = false;
      saveRef.current();
    };
  }, []);

  function change(field, value) {
    latest.current = { ...latest.current, [field]: value };
    setDraft(latest.current);
    clearTimeout(timer.current);
    // A new analysis waits for a description, so typing a name alone creates nothing.
    if (id.current || latest.current.description.trim()) {
      timer.current = setTimeout(() => saveRef.current(), AUTOSAVE_DELAY_MS);
    }
  }

  return { draft, change, save, status };
}

function SystemEditor({ analysis, goTo, registerBeforeLeave }) {
  const { draft, change, save, status } = useAutosave(analysis);
  const [confirmExample, setConfirmExample] = useState(false);
  const { name, description } = draft;

  useEffect(() => {
    registerBeforeLeave(save);
    return () => registerBeforeLeave(null);
  }, [registerBeforeLeave, save]);

  function applyExample() {
    change('description', EXAMPLE_SYSTEM.description);
    if (!name.trim()) change('name', EXAMPLE_SYSTEM.name);
    setConfirmExample(false);
  }

  function insertOutline() {
    change('description', description.trim() ? `${description.trimEnd()}\n\n${OUTLINE}` : OUTLINE);
  }

  return (
    <section className="space-y-5">
      <h2 className={stepTitle}>
        <label htmlFor="analysis-description">{QUESTION}</label>
      </h2>
      <label className="block text-sm font-medium text-gray-700">
        System name
        <input
          dir="auto"
          maxLength={300}
          value={name}
          onChange={(event) => change('name', event.target.value)}
          onBlur={() => (analysis || description.trim()) && save()}
          placeholder="For example, Smart Parking"
          className={textInput}
        />
      </label>
      <textarea
        id="analysis-description"
        dir="auto"
        maxLength={DESCRIPTION_MAX_LENGTH}
        value={description}
        onChange={(event) => change('description', event.target.value)}
        onBlur={() => save()}
        placeholder="Describe what it does, who uses and operates it, what it is made of, and how it is run."
        // Fills the screen below the other controls and scrolls inside.
        className={`${textInput} h-[calc(100dvh-35rem)] min-h-[10rem] resize-none overflow-y-auto rounded-xl p-4 leading-relaxed`}
      />
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <button type="button" onClick={insertOutline} className={linkButton}>
          Insert an outline
        </button>
        <button
          type="button"
          onClick={() => (description.trim() ? setConfirmExample(true) : applyExample())}
          className={linkButton}
        >
          Try an example
        </button>
        <p aria-live="polite" className="ml-auto text-xs text-gray-500">
          {status.phase === 'saving' && 'Saving…'}
          {status.phase === 'saved' && 'Saved'}
          {status.phase === 'failed' && 'Not saved'}
        </p>
      </div>

      {confirmExample && (
        <div className="flex flex-wrap items-center gap-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          The example replaces what you wrote.
          <button type="button" onClick={applyExample} className={secondaryButton}>
            Replace with the example
          </button>
          <button type="button" onClick={() => setConfirmExample(false)} className={linkButton}>
            Keep mine
          </button>
        </div>
      )}
      {status.phase === 'failed' && <ErrorBanner error={status.error} />}

      <div className="flex justify-end border-t border-gray-200 pt-5">
        <button
          type="button"
          disabled={!name.trim() || !description.trim()}
          onClick={() => goTo('extract')}
          className={primaryButton}
        >
          Continue to extraction →
        </button>
      </div>
    </section>
  );
}

export default function SystemStep(props) {
  const { canRunAnalyses } = useAuth();
  const { analysis } = props;
  if (analysis && (analysis.locked || !canRunAnalyses)) return <ReadOnlySystem analysis={analysis} />;
  return <SystemEditor {...props} />;
}
