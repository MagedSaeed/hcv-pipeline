import { STEPS } from './steps';

// `working` names the step whose model is running, if any.
export default function Stepper({ steps, current, locked, reachable, working, onSelect }) {
  return (
    <nav aria-label="Analysis steps" className="print:hidden">
      <ol className="flex items-start">
        {STEPS.map(({ key, title }, index) => {
          const isCurrent = key === current;
          const done = Boolean(steps?.[key]);
          const isLock = key === 'system' && locked;
          const breathe = key === working ? 'animate-breathe motion-reduce:animate-none' : '';
          const blocked = !isCurrent && !reachable[key];
          let circle = blocked ? 'border-gray-200 bg-white text-gray-300' : 'border-gray-300 bg-white text-gray-500';
          if (done) circle = 'border-brand-200 bg-brand-50 text-brand-700';
          if (isCurrent) circle = 'border-brand-600 bg-brand-600 text-white';
          return (
            <li key={key} className="relative flex-1">
              {index > 0 && <span aria-hidden="true" className="absolute right-1/2 top-4 z-0 h-px w-full bg-gray-200" />}
              <button
                type="button"
                aria-current={isCurrent ? 'step' : undefined}
                disabled={blocked}
                onClick={() => !isCurrent && onSelect(key)}
                className="group relative z-10 flex w-full flex-col items-center gap-1.5 disabled:cursor-not-allowed"
              >
                <span
                  aria-hidden="true"
                  className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm font-semibold ${circle} ${breathe}`}
                >
                  {done && !isCurrent ? '✓' : index + 1}
                </span>
                <span className={`text-xs sm:text-sm ${isCurrent ? 'font-semibold text-gray-900' : blocked ? 'text-gray-400' : 'text-gray-600'} ${breathe}`}>
                  {title}
                  {isLock && <span aria-hidden="true"> 🔒</span>}
                </span>
                {isLock && <span className="sr-only">(locked)</span>}
                {key === working && <span className="sr-only">(working)</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
