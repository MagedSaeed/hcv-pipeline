import { STEPS } from './steps';

export default function Stepper({ steps, current, locked, reachable, onSelect }) {
  return (
    <nav aria-label="Analysis steps" className="print:hidden">
      <ol className="flex items-start">
        {STEPS.map(({ key, title }, index) => {
          const isCurrent = key === current;
          const done = Boolean(steps?.[key]);
          const isLock = key === 'system' && locked;
          let circle = 'border-gray-300 bg-white text-gray-500';
          if (done) circle = 'border-brand-200 bg-brand-50 text-brand-700';
          if (isCurrent) circle = 'border-brand-600 bg-brand-600 text-white';
          return (
            <li key={key} className="relative flex-1">
              {index > 0 && <span aria-hidden="true" className="absolute right-1/2 top-4 z-0 h-px w-full bg-gray-200" />}
              <button
                type="button"
                aria-current={isCurrent ? 'step' : undefined}
                disabled={!isCurrent && !reachable[key]}
                onClick={() => !isCurrent && onSelect(key)}
                className="group relative z-10 flex w-full flex-col items-center gap-1.5 disabled:cursor-not-allowed"
              >
                <span
                  aria-hidden="true"
                  className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm font-semibold ${circle}`}
                >
                  {done && !isCurrent ? '✓' : index + 1}
                </span>
                <span className={`text-xs sm:text-sm ${isCurrent ? 'font-semibold text-gray-900' : 'text-gray-600'}`}>
                  {title}
                  {isLock && <span aria-hidden="true"> 🔒</span>}
                </span>
                {isLock && <span className="sr-only">(locked)</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
