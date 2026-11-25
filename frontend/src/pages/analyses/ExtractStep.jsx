import { useState } from 'react';
import {
  activeRunOf,
  useAnalysisOptions,
  useApproveAsserted,
  useAddItem,
  useApproveSection,
  useDeleteItem,
  useExtract,
  useUpdateItem,
} from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorBanner from '../../components/ErrorBanner';
import RunStatus from '../../components/analyses/RunStatus';
import SettingsPanel from '../../components/analyses/SettingsPanel';
import SetupSummary from '../../components/analyses/SetupSummary';
import Tab from '../../components/analyses/Tab';
import { primaryButton, secondaryButton, stepTitle } from '../../components/analyses/styles';
import useSettingsSaver from '../../components/analyses/useSettingsSaver';

const CONTROL_SECTION = '8. Controls';
const SECTIONS = [
  '1. People and organizations',
  '2. System components',
  '3. Use and responsibility',
  '4. Data flows',
  '5. Sign-in and access',
  '6. Automated decisions',
  '7. Settings',
  '8. Controls',
];
const DECISION_STYLE = {
  approved: 'border-l-emerald-500 bg-white',
  rejected: 'border-l-gray-300 bg-gray-50 opacity-70',
  pending: 'border-l-amber-400 bg-white',
};
const listBox = 'max-h-[calc(100dvh-44rem)] min-h-[16rem] overflow-y-auto rounded-xl border border-gray-200 p-3';
const decisionButton = 'grid h-7 w-7 place-items-center rounded-md border text-sm transition-colors';
const quietField =
  'w-full min-w-0 truncate rounded border border-transparent bg-transparent py-0.5 font-mono text-sm text-gray-900 hover:border-gray-300 focus:border-brand-500 focus:bg-white focus:outline-none';
const smallSelect =
  'w-full min-w-0 truncate rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500';

// The structured part of the body; the original description follows it.
function structuredPart(body = '') {
  return body.split('\n\n## Original description')[0];
}

function words(field) {
  return field.replace(/_/g, ' ');
}

// One value control per kind of field; changes are saved as they are made.
function ValueEditor({ item, fieldSpec, controls, elements, onSave }) {
  const [typed, setTyped] = useState(null);

  if (item.kind === 'control') {
    return (
      <span className="grid grid-cols-[minmax(0,1fr)_6rem] gap-2">
        <select
          aria-label="Value"
          value={item.value}
          onChange={(e) => onSave({ value: e.target.value })}
          className={smallSelect}
        >
          {!controls.includes(item.value) && <option value={item.value}>{item.value}</option>}
          {controls.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <select
          aria-label="State"
          value={item.state}
          onChange={(e) => onSave({ state: e.target.value })}
          className={smallSelect}
        >
          <option value="present">present</option>
          <option value="absent">absent</option>
        </select>
      </span>
    );
  }
  if (!fieldSpec) return <span className="font-mono text-sm text-gray-700">{item.value}</span>;
  if (fieldSpec.value_kind === 'number') {
    return (
      <input
        aria-label="Value"
        type="number"
        min="0"
        value={typed ?? item.value}
        onChange={(e) => setTyped(e.target.value)}
        onBlur={() => {
          if (typed !== null && typed !== item.value) onSave({ value: typed });
          setTyped(null);
        }}
        className={`${smallSelect} max-w-[8rem]`}
      />
    );
  }
  const options =
    fieldSpec.value_kind === 'boolean'
      ? ['true', 'false']
      : fieldSpec.value_kind === 'element'
        ? elements
        : fieldSpec.allowed;
  return (
    <select
      aria-label="Value"
      value={item.value}
      onChange={(e) => onSave({ value: e.target.value })}
      className={smallSelect}
    >
      {!options.includes(item.value) && <option value={item.value}>{item.value}</option>}
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

// An element's id, saved on Enter or on leaving the field.
function IdInput({ value, onSave }) {
  const [typed, setTyped] = useState(null);
  const commit = () => {
    const next = typed?.trim();
    if (next && next !== value) onSave({ ref: next });
    setTyped(null);
  };
  return (
    <input
      aria-label="Id"
      value={typed ?? value}
      spellCheck={false}
      onChange={(e) => setTyped(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur();
        if (e.key === 'Escape') setTyped(null);
      }}
      className={`${quietField} px-1`}
    />
  );
}

function ElementSelect({ value, elements, onChange, className }) {
  return (
    <select aria-label="Id" value={value} onChange={(e) => onChange(e.target.value)} className={className}>
      {!elements.includes(value) && <option value={value}>{value || 'Choose an element…'}</option>}
      {elements.map((ref) => (
        <option key={ref} value={ref}>
          {ref}
        </option>
      ))}
    </select>
  );
}

function ItemRow({ item, options, elements, editable, update, remove }) {
  const label = `${item.ref} ${words(item.field)}`;
  const save = (body) => update.mutate({ itemId: item.id, ...body });
  return (
    <div
      role="group"
      aria-label={label}
      className={`space-y-1.5 border-l-4 px-4 py-3 ${DECISION_STYLE[item.decision]}`}
    >
      {/* Fixed columns: what it is, its value, how sure, and the decision; stacked on narrow screens. */}
      <div className="grid grid-cols-1 items-center gap-x-3 gap-y-1.5 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_4.5rem_auto]">
        <span className="min-w-0">
          {!editable ? (
            <span className="block truncate font-mono text-sm text-gray-900" title={item.ref}>
              {item.ref || '(no id)'}
            </span>
          ) : item.kind === 'element' ? (
            <IdInput value={item.ref} onSave={save} />
          ) : (
            <ElementSelect
              value={item.ref}
              elements={elements}
              onChange={(ref) => save({ ref })}
              className={quietField}
            />
          )}
          <span className="block truncate text-xs text-gray-500" title={words(item.field)}>
            {item.kind === 'control' ? 'control' : words(item.field)}
          </span>
        </span>
        <span className="min-w-0">
          {editable ? (
            <ValueEditor
              item={item}
              fieldSpec={options?.fields[item.field]}
              controls={options?.controls ?? []}
              elements={elements}
              onSave={save}
            />
          ) : (
            <span className="break-words font-mono text-sm text-gray-800">
              {item.value}
              {item.state && ` ${item.state}`}
            </span>
          )}
        </span>
        {item.added ? (
          <span
            title="Added manually"
            className="justify-self-start rounded-full bg-brand-50 px-2 py-0.5 text-center text-xs text-brand-700 sm:w-full"
          >
            Added
          </span>
        ) : (
          <span
            className={`justify-self-start rounded-full px-2 py-0.5 text-center text-xs sm:w-full ${
              item.fact_type === 'Possible' ? 'bg-amber-50 text-amber-800' : 'bg-gray-100 text-gray-600'
            }`}
          >
            {item.fact_type}
          </span>
        )}
        {editable && item.added && (
          <span className="flex justify-end">
            <button
              type="button"
              aria-label="Delete"
              title="Delete"
              onClick={() => remove.mutate(item.id)}
              className={`${decisionButton} border-gray-300 text-gray-500 hover:border-red-600 hover:text-red-700`}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 16 16"
                className="h-3.5 w-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path
                  d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </span>
        )}
        {editable && !item.added && (
          <span className="flex gap-1">
            <button
              type="button"
              aria-label="Approve"
              aria-pressed={item.decision === 'approved'}
              disabled={Boolean(item.problem)}
              title={item.problem ? 'Edit or reject this value first' : 'Approve'}
              onClick={() =>
                save({
                  decision: item.decision === 'approved' ? 'pending' : 'approved',
                })
              }
              className={`${decisionButton} disabled:cursor-not-allowed disabled:opacity-40 ${
                item.decision === 'approved'
                  ? 'border-emerald-600 bg-emerald-600 text-white'
                  : 'border-gray-300 text-gray-600 hover:border-emerald-600 hover:text-emerald-700'
              }`}
            >
              ✓
            </button>
            <button
              type="button"
              aria-label="Reject"
              aria-pressed={item.decision === 'rejected'}
              title="Reject"
              onClick={() =>
                save({
                  decision: item.decision === 'rejected' ? 'pending' : 'rejected',
                })
              }
              className={`${decisionButton} ${
                item.decision === 'rejected'
                  ? 'border-gray-600 bg-gray-600 text-white'
                  : 'border-gray-300 text-gray-600 hover:border-gray-600 hover:text-gray-900'
              }`}
            >
              ✕
            </button>
          </span>
        )}
      </div>
      {item.reason && (
        <p dir="auto" className="break-words text-xs italic text-gray-600">
          {item.reason}
        </p>
      )}
      {item.other_reading && (
        <p dir="auto" className="break-words text-xs text-gray-500">
          Other reading: {item.other_reading}
        </p>
      )}
      {item.edited && (
        <p className="break-words text-xs text-brand-700">
          Edited · the model said {item.original_value}
          {item.original_state && item.original_state !== item.state && ` ${item.original_state}`}
        </p>
      )}
      {item.problem && <p className="text-xs text-red-700">{item.problem}</p>}
      {item.refers_to_rejected && <p className="text-xs text-gray-500">Refers to a rejected element</p>}
    </div>
  );
}

// A new value for one section: its field, the element it is about, and its value.
function AddValueForm({ section, step, elements, add, onDone }) {
  const fields =
    section === CONTROL_SECTION
      ? ['control']
      : Object.keys(step.fields).filter((name) => step.fields[name].section === section);
  const [field, setField] = useState(fields[0]);
  const [ref, setRef] = useState('');
  const [value, setValue] = useState('');
  const [itemState, setItemState] = useState('present');
  const spec = field === 'control' ? null : step.fields[field];
  const isElement = spec?.kind === 'element';
  const choices =
    field === 'control'
      ? step.controls
      : spec.value_kind === 'boolean'
        ? ['true', 'false']
        : spec.value_kind === 'element'
          ? elements
          : spec.allowed;

  function submit(event) {
    event.preventDefault();
    const body = { field, ref: ref.trim(), value: value.trim() };
    if (field === 'control') body.state = itemState;
    add.mutate(body, { onSuccess: onDone });
  }

  return (
    <form
      aria-label="Add a value"
      onSubmit={submit}
      className="space-y-3 border-l-4 border-l-brand-500 bg-white px-4 py-3"
    >
      <div className="grid grid-cols-1 gap-x-3 gap-y-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]">
        {fields.length > 1 && (
          <label className="space-y-1 sm:col-span-2">
            <span className="block text-xs text-gray-500">Field</span>
            <select
              aria-label="Field"
              value={field}
              onChange={(e) => {
                const next = e.target.value;
                if (step.fields[next].kind !== spec.kind) setRef('');
                setField(next);
                setValue('');
              }}
              className={`${smallSelect} sm:max-w-[16rem]`}
            >
              {fields.map((name) => (
                <option key={name} value={name}>
                  {words(name)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="min-w-0 space-y-1">
          <span className="block text-xs text-gray-500">{isElement ? 'New id' : 'About'}</span>
          {isElement ? (
            <input
              aria-label="Id"
              value={ref}
              spellCheck={false}
              placeholder="e.g. KioskScreen"
              onChange={(e) => setRef(e.target.value)}
              className={`${smallSelect} font-mono`}
            />
          ) : (
            <ElementSelect value={ref} elements={elements} onChange={setRef} className={`${smallSelect} font-mono`} />
          )}
        </label>
        <div className="min-w-0 space-y-1">
          <span className="block text-xs text-gray-500">Value</span>
          {spec?.value_kind === 'number' ? (
            <input
              aria-label="Value"
              type="number"
              min="0"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className={`${smallSelect} max-w-[8rem]`}
            />
          ) : (
            <span className={field === 'control' ? 'grid grid-cols-[minmax(0,1fr)_6rem] gap-2' : 'block'}>
              <select
                aria-label="Value"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className={smallSelect}
              >
                <option value="" disabled>
                  Choose…
                </option>
                {choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
              {field === 'control' && (
                <select
                  aria-label="State"
                  value={itemState}
                  onChange={(e) => setItemState(e.target.value)}
                  className={smallSelect}
                >
                  <option value="present">present</option>
                  <option value="absent">absent</option>
                </select>
              )}
            </span>
          )}
        </div>
      </div>
      {add.isError && <ErrorBanner error={add.error} />}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className={secondaryButton}>
          Cancel
        </button>
        <button type="submit" disabled={!ref.trim() || !value.trim() || add.isPending} className={primaryButton}>
          Add
        </button>
      </div>
    </form>
  );
}

function Gaps({ gaps }) {
  const areas = gaps.not_assessable ?? [];
  const statements = gaps.no_term ?? [];
  if (areas.length + statements.length === 0) return null;
  return (
    <section
      aria-label="Not covered by the description"
      className="rounded-xl border border-gray-200 bg-white p-4 text-sm"
    >
      <h3 className="font-medium text-gray-900">Not covered by the description</h3>
      <ul className="mt-2 space-y-1">
        {areas.map((gap) => (
          <li key={`a-${gap.area}`}>
            <span className="text-gray-800">{gap.area}</span>
            {gap.reason && <span className="text-gray-500"> · {gap.reason}</span>}
          </li>
        ))}
        {statements.map((gap) => (
          <li key={`n-${gap.statement}`}>
            <span dir="auto" className="text-gray-800">
              {gap.statement}
            </span>
            <span className="text-gray-500"> · no matching term{gap.reason && `: ${gap.reason}`}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function ExtractStep({ analysis, goTo }) {
  const { isStaff } = useAuth();
  const options = useAnalysisOptions(analysis.taxonomy.id);
  const saver = useSettingsSaver(analysis, 'extraction');
  const extract = useExtract(analysis.id);
  const update = useUpdateItem(analysis.id);
  const approveAll = useApproveAsserted();
  const approveSection = useApproveSection(analysis.id);
  const addItem = useAddItem(analysis.id);
  const deleteItem = useDeleteItem(analysis.id);
  const [adding, setAdding] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [view, setView] = useState('fields');

  const { extraction } = analysis;
  const step = options.data?.extraction;
  const running = Boolean(activeRunOf(analysis));
  const editable = isStaff && !analysis.locked && !running;
  const elements = extraction.items.filter((item) => item.kind === 'element' && item.ref).map((item) => item.ref);
  const counts = {
    approved: extraction.items.filter((item) => item.decision === 'approved').length,
    rejected: extraction.items.filter((item) => item.decision === 'rejected').length,
  };
  const ready = analysis.locked || (extraction.status === 'current' && extraction.pending === 0);

  async function start() {
    setConfirming(false);
    if (await saver.flush()) extract.mutate();
  }

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h2 className={stepTitle}>Extract the system’s values</h2>
        <SetupSummary
          settings={saver.settings}
          defaults={step?.defaults}
          prompting={step?.prompt ?? 'System description extractor'}
          onChange={editable ? () => setPanelOpen(true) : undefined}
        />
      </div>

      <RunStatus analysisId={analysis.id} run={analysis.latest_runs.extract} working="Reading the description" typical="This can take a while" />
      {extract.isError && <ErrorBanner error={extract.error} />}
      {update.isError && <ErrorBanner error={update.error} />}
      {approveSection.isError && <ErrorBanner error={approveSection.error} />}
      {deleteItem.isError && <ErrorBanner error={deleteItem.error} />}
      {saver.error && !panelOpen && <ErrorBanner error={saver.error} />}

      {analysis.locked && <p className="text-sm text-gray-600">These values are fixed once generation started.</p>}
      {extraction.status === 'stale' && !analysis.locked && (
        <p role="status" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          The description changed since extraction. Extract again before generating.
        </p>
      )}

      {extraction.status === 'none' && !running && (
        <div className="rounded-xl border border-dashed border-gray-300 px-6 py-10 text-center">
          <p className="text-sm text-gray-600">
            The model reads your description and lists its people, components, data flows, sign-in and controls. You
            then approve, edit or reject each value.
          </p>
          {editable && (
            <button type="button" onClick={start} disabled={extract.isPending} className={`${primaryButton} mt-4`}>
              Extract values
            </button>
          )}
        </div>
      )}

      {extraction.items.length > 0 && (
        <>
          <p className="text-sm text-gray-600">
            {extraction.items.length} values · {counts.approved} approved · {counts.rejected} rejected ·{' '}
            {extraction.pending} waiting
          </p>
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-gray-200">
            <div role="tablist" aria-label="Extract view" className="flex gap-6">
              <Tab selected={view === 'fields'} onSelect={() => setView('fields')}>
                Fields
              </Tab>
              <Tab selected={view === 'text'} onSelect={() => setView('text')}>
                As text
              </Tab>
            </div>
            {editable && (
              <div className="flex flex-wrap gap-2 pb-2">
                <button
                  type="button"
                  onClick={() => approveAll.mutate(analysis.id)}
                  disabled={approveAll.isPending || extraction.pending === 0}
                  className={secondaryButton}
                >
                  Approve all Asserted
                </button>
                <button type="button" onClick={() => setConfirming(true)} className={secondaryButton}>
                  Extract again
                </button>
              </div>
            )}
          </div>
          {confirming && (
            <ConfirmDialog
              title="Replace the extracted values?"
              message="A new extraction replaces every value, including your edits and decisions."
              confirmLabel="Replace the values"
              onConfirm={start}
              onCancel={() => setConfirming(false)}
            />
          )}
          {view === 'text' ? (
            <div role="tabpanel" className={`${listBox} animate-fade-in bg-white motion-reduce:animate-none`}>
              <pre
                dir="auto"
                className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-gray-800"
              >
                {structuredPart(extraction.body)}
              </pre>
              <p className="mt-3 text-xs text-gray-500">
                Only approved values appear, followed by your original description. This is what the generator and the
                labeller read.
              </p>
            </div>
          ) : (
            <div
              role="tabpanel"
              className={`${listBox} animate-fade-in space-y-5 bg-gray-50 motion-reduce:animate-none`}
            >
              {SECTIONS.map((section) => {
                const rows = extraction.items.filter((item) => item.section === section);
                if (rows.length === 0 && !editable) return null;
                const done = rows.filter((item) => item.decision === 'approved').length;
                const approvable = rows.some((item) => item.decision === 'pending' && !item.problem);
                return (
                  <section key={section} aria-label={section} className="space-y-1.5">
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1">
                      <h3 className="text-sm font-semibold text-gray-900">{section}</h3>
                      <span className="flex items-center gap-3 text-sm text-gray-500">
                        {rows.length > 0 && `${done} of ${rows.length} approved`}
                        {editable && approvable && (
                          <button
                            type="button"
                            onClick={() => approveSection.mutate(section)}
                            disabled={approveSection.isPending}
                            className="rounded-md px-2 py-0.5 text-sm font-medium text-brand-700 hover:bg-brand-50 disabled:opacity-50"
                          >
                            Approve section
                          </button>
                        )}
                      </span>
                    </div>
                    <div className="divide-y divide-gray-100 overflow-hidden rounded-lg border border-gray-200 bg-white">
                      {rows.map((item) => (
                        <ItemRow
                          key={item.id}
                          item={item}
                          options={step}
                          elements={elements}
                          editable={editable}
                          update={update}
                          remove={deleteItem}
                        />
                      ))}
                      {editable && step && adding === section && (
                        <AddValueForm
                          section={section}
                          step={step}
                          elements={elements}
                          add={addItem}
                          onDone={() => {
                            addItem.reset();
                            setAdding(null);
                          }}
                        />
                      )}
                      {editable && step && adding !== section && (
                        <div className="flex items-center justify-between gap-3 px-4 py-2">
                          {rows.length === 0 && <span className="text-xs text-gray-500">Nothing extracted here.</span>}
                          <button
                            type="button"
                            onClick={() => {
                              addItem.reset();
                              setAdding(section);
                            }}
                            className="ml-auto rounded-md px-2 py-0.5 text-sm font-medium text-brand-700 hover:bg-brand-50"
                          >
                            + Add a value
                          </button>
                        </div>
                      )}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
          <Gaps gaps={extraction.gaps} />
        </>
      )}

      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-gray-200 pt-5">
        {!ready && extraction.pending > 0 && (
          <p className="text-sm text-gray-500">
            {extraction.pending} {extraction.pending === 1 ? 'value still needs' : 'values still need'} approving or
            rejecting.
          </p>
        )}
        <button type="button" disabled={!ready || running} onClick={() => goTo('generate')} className={primaryButton}>
          Continue to generate →
        </button>
      </div>

      {panelOpen && editable && (
        <SettingsPanel
          kind="extraction"
          analysis={analysis}
          step={step}
          saver={saver}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </section>
  );
}
