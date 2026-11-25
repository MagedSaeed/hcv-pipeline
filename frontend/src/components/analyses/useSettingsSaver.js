import { useCallback, useRef, useState } from 'react';
import { useUpdateAnalysis } from '../../api/analyses';

// Saves one step's settings on every change, in order; flush() waits for the last.
export default function useSettingsSaver(analysis, kind) {
  const field = `${kind}_settings`;
  const { mutateAsync: update } = useUpdateAnalysis();
  const [local, setLocal] = useState(null);
  const [error, setError] = useState(null);
  const pending = useRef({ count: 0, latest: null, ok: true, queue: Promise.resolve() });
  const settings = local ?? analysis[field];

  function save(changes) {
    const state = pending.current;
    const next = { ...(state.latest ?? analysis[field]), ...changes };
    state.latest = next;
    state.count += 1;
    setLocal(next);
    setError(null);
    state.queue = state.queue.then(async () => {
      try {
        await update({ id: analysis.id, [field]: next });
        state.ok = true;
      } catch (failure) {
        state.ok = false;
        setError(failure);
      }
      state.count -= 1;
      if (state.count === 0) {
        state.latest = null;
        setLocal(null);
      }
    });
  }

  const flush = useCallback(async () => {
    const state = pending.current;
    await state.queue;
    const { ok } = state;
    state.ok = true;
    return ok;
  }, []);

  return { settings, save, flush, error };
}
