const TONES = {
  recommended: 'bg-brand-50 text-brand-700 border-brand-200',
  custom: 'bg-gray-100 text-gray-700 border-gray-200',
};

export default function Badge({ tone = 'recommended', children }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}
