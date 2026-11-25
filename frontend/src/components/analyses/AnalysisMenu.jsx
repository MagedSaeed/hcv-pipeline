import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDeleteAnalysis, useStartNew, useUpdateAnalysis } from '../../api/analyses';
import ConfirmDialog from '../ConfirmDialog';
import ErrorBanner from '../ErrorBanner';
import Modal from '../Modal';
import ActionMenu from './ActionMenu';
import { primaryButton, textInput } from './styles';

// Rename, Start new and Delete, as used by the list and an analysis's header.
export default function AnalysisMenu({ analysis, onDeleted }) {
  const navigate = useNavigate();
  const update = useUpdateAnalysis();
  const startNew = useStartNew();
  const remove = useDeleteAnalysis();
  const [dialog, setDialog] = useState(null);
  const [name, setName] = useState('');
  const title = analysis.name || 'Untitled system';

  async function handleStartNew() {
    try {
      const copy = await startNew.mutateAsync(analysis.id);
      navigate(`/analyses/${copy.id}/system`);
    } catch {
      setDialog('error');
    }
  }

  function handleRename(event) {
    event.preventDefault();
    update.mutate({ id: analysis.id, name: name.trim() }, { onSuccess: () => setDialog(null) });
  }

  function handleDelete() {
    remove.mutate(analysis.id, {
      onSuccess: () => {
        setDialog(null);
        onDeleted?.();
      },
    });
  }

  return (
    <>
      <ActionMenu
        label={`Actions for ${title}`}
        items={[
          {
            label: 'Rename',
            onSelect: () => {
              setName(analysis.name);
              update.reset();
              setDialog('rename');
            },
          },
          { label: 'Start new from this description', onSelect: handleStartNew },
          { label: 'Delete', danger: true, onSelect: () => setDialog('delete') },
        ]}
      />
      {dialog === 'rename' && (
        <Modal title="Rename" onClose={() => setDialog(null)} centered>
          <form onSubmit={handleRename} className="space-y-4">
            <label className="block text-sm font-medium text-gray-700">
              Name
              <input
                dir="auto"
                autoFocus
                required
                maxLength={300}
                value={name}
                onChange={(event) => setName(event.target.value)}
                className={textInput}
              />
            </label>
            {update.isError && <ErrorBanner error={update.error} />}
            <button type="submit" disabled={update.isPending || !name.trim()} className={primaryButton}>
              Save
            </button>
          </form>
        </Modal>
      )}
      {dialog === 'delete' && (
        <Modal title="Delete analysis" onClose={() => setDialog(null)} centered>
          <div className="space-y-3">
            <ConfirmDialog
              title={`Delete "${title}"?`}
              message="This removes its description, scenarios, the vulnerabilities found and its runs. It can't be undone."
              confirmLabel="Delete"
              onConfirm={handleDelete}
              onCancel={() => setDialog(null)}
              pending={remove.isPending}
            />
            {remove.isError && <ErrorBanner error={remove.error} />}
          </div>
        </Modal>
      )}
      {dialog === 'error' && (
        <Modal title="Could not start a new analysis" onClose={() => setDialog(null)} centered>
          <ErrorBanner error={startNew.error} />
        </Modal>
      )}
    </>
  );
}
