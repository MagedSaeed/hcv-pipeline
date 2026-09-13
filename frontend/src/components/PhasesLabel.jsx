// An experiment or pinned snapshot's `phases`: [2] or [2, 3]. Missing means both
// (every run launched before the field existed ran both phases).
export default function PhasesLabel({ phases }) {
  const list = phases ?? [2, 3];
  return list.includes(3) ? 'Phase 2 (generate) → Phase 3 (label)' : 'Phase 2 only (generate, no labelling)';
}
