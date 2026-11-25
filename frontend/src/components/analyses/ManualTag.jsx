export function SetAsideTag() {
  return (
    <span className="inline-block whitespace-nowrap rounded-full border border-gray-300 bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
      Set aside
    </span>
  );
}

export default function ManualTag() {
  return (
    <span className="inline-block whitespace-nowrap rounded-full border border-teal-300 bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-800">
      Added manually
    </span>
  );
}
