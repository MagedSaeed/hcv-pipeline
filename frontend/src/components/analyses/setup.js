export const TEMPERATURE_HINT = 'This model may ignore temperature';

// Only when the catalogue lists the model without temperature; unknown means no hint.
export function mayIgnoreTemperature(models, modelId) {
  const supported = models?.find((model) => model.id === modelId)?.supported_parameters;
  return Array.isArray(supported) && !supported.includes('temperature');
}

// "Anthropic: Claude Sonnet 5" reads as "Claude Sonnet 5".
export function modelName(models, modelId) {
  const name = models?.find((model) => model.id === modelId)?.name;
  return name ? name.replace(/^[^:]+:\s*/, '') : modelId.split('/').pop();
}

export function formatTemperature(value) {
  return Number.isInteger(value) ? value.toFixed(1) : String(value);
}

export function promptingText(exampleCount) {
  if (!exampleCount) return 'zero-shot';
  return `few-shot, ${exampleCount} worked ${exampleCount === 1 ? 'example' : 'examples'}`;
}

export function isRecommended(settings, defaults) {
  return Object.entries(defaults).every(([key, value]) => JSON.stringify(settings[key]) === JSON.stringify(value));
}

export function taxonomyName(taxonomy) {
  return `${taxonomy.vocabulary_id} v${taxonomy.version}`;
}
